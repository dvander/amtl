# vim: set ts=2 sw=2 tw=99 et:
import os, argparse
from collections import deque
from collections import namedtuple
from pygments.token import Token as TokenKind
from pygments.lexers.c_cpp import CppLexer

def main():
  parser = argparse.ArgumentParser()
  parser.add_argument('files', type=str, nargs='+',
                      help='File list')

  args = parser.parse_args()
  parse_files(args)

def parse_files(args):
  for path in args.files:
    if os.path.isdir(path):
      continue
    with open(path, 'rb') as fp:
      text = fp.read()
    lexer = CppLexer()
    tokens = lexer.get_tokens_unprocessed(text)
    inserter = TokenAnalyzer(tokens)
    changes = inserter.parse()
    if not changes:
      continue
    print(path)
    text = change_text(text, changes)
    with open(path, 'wb') as fp:
      fp.write(text)

Token = namedtuple('Token', ['pos', 'kind', 'text', 'line', 'col'])

def is_token(tok, c):
  return tok.kind == TokenKind.Punctuation and tok.text == c

def is_whitespace(tok):
  if tok.kind in TokenKind.Comment:
    return True
  if tok.kind == TokenKind.Text:
    return tok.text.isspace()
  return False

def split_text_by_newlines(text):
  pieces = text.split('\n')
  output = []
  for index, piece in enumerate(pieces):
    if len(piece) > 0:
      output.append(piece)
    if index != len(pieces) - 1:
      output.append('\n')
  return output

def change_text(text, changes):
  characters = list(text)
  additions = []
  for cmd, tok, c in changes:
    if cmd == 'replace':
      characters[tok.pos] = c
    elif cmd == 'insert':
      additions.append((tok.pos, c))
    elif cmd == 'insert-before':
      additions.append((tok.pos - 1, c))
    elif cmd == 'delete':
      for i in range(len(tok.text)):
        characters[tok.pos + i] = ''
    elif cmd == 'truncate-line-at':
      # Remove the given character and all preceding whitespace.
      characters[tok.pos] = ''
      pos = tok.pos - 1
      while pos >= 0 and characters[pos] == ' ':
        characters[pos] = ''
        pos -= 1
    else:
      raise Exception('not yet implemented')

  # Process additions in reverse to make this algorithm easier.
  additions = sorted(additions, key=lambda entry: entry[0])
  while additions:
    pos, c = additions.pop()
    characters.insert(pos + 1, c)
  return ''.join(characters)

kProcessIgnored = 'ignore'
kProcessContinue = 'continue'
kProcessCompleted = 'completed'
class PatternMatcher(object):
  def __init__(self, analyzer):
    self.analyzer = analyzer

class TokenAnalyzer(object):
  def __init__(self, tokens):
    self.raw_readahead_ = deque()
    self.tokens_ = tokens
    self.changes_ = []
    self.line_ = 1
    self.line_indent_ = ''
    self.determined_indent_ = False
    self.first_token_on_line_ = None
    self.matcher_ = None
    self.line_pos_ = 0
    self.block_type_ = []
    self.waiting_for_brace_ = None
    self.paren_level_ = 0
    # If not None, this is the paren level at which a constructor initializer
    # was found. Will not work if the initializer contains a nested function
    # which declares a class with an initializer, which would be insane.
    self.in_ctor_init_ = None

  def parse(self):
    while True:
      tok = self.get_next()
      if tok is None:
        break

      self.preprocess_token(tok)
      self.process_token(tok)

    return self.changes_

  def process_token(self, tok):
    status = kProcessIgnored
    changed = False
    if self.matcher_ is not None:
      status, changed = self.matcher_.process_token(tok)

    if status == kProcessCompleted:
      self.matcher_ = None

    if self.matcher_ is None and not changed:
      self.find_pattern_matcher(tok)

  def preprocess_token(self, tok):
    if is_token(tok, '('):
      self.paren_level_ += 1
    elif is_token(tok, ')'):
      self.paren_level_ -= 1
    elif is_token(tok, '{'):
      if self.paren_level_ == self.in_ctor_init_:
        self.in_ctor_init_ = None

    if self.waiting_for_brace_:
      if is_token(tok, '{'):
        self.block_type_.append(tok)
        self.waiting_for_brace_ = None
      elif is_token(tok, ';'):
        self.waiting_for_brace_ = None
      return

    if tok.kind == TokenKind.Keyword:
      if tok.text in ['class', 'struct', 'union']:
        self.waiting_for_brace_ = 'class'
      return

    if is_token(tok, '{'):
      self.block_type_.append(tok)
    elif is_token(tok, '}'):
      assert len(self.block_type_) > 1
      self.block_type_.pop()
    elif is_token(tok, ':'):
      if self.block_type == 'class':
        self.in_ctor_init_ = self.paren_level_
    return

  def find_pattern_matcher(self, tok):
    for cls in kAllPatternMatchers:
      self.matcher_ = cls.identify(self, tok)
      if self.matcher_ is not None:
        return

  def add_change(self, change):
    self.changes_.append(change)

  def insert(self, tok, rep):
    self.add_change(('insert', tok, rep))

  def insert_before(self, tok, rep):
    self.add_change(('insert-before', tok, rep))

  def delete(self, tok):
    self.add_change(('delete', tok, None))

  def replace(self, tok, rep):
    self.add_change(('replace', tok, rep))

  def truncate_line_at(self, tok):
    self.add_change(('truncate-line-at', tok, None))

  # Read the next raw token from the lexer, potentially splitting it into
  # multiple tokens if needed.
  def read_next_raw_token(self):
    if self.raw_readahead_:
      return self.raw_readahead_.popleft()

    pos, kind, text = next(self.tokens_)

    # Split newlines into separate tokens to make things more accurate.
    if '\n' in text:
      assert (kind in TokenKind.Comment) or (kind == TokenKind.Text)
      for piece in split_text_by_newlines(text):
        if piece == '\n':
          self.raw_readahead_.append((pos, TokenKind.Text, piece))
        else:
          self.raw_readahead_.append((pos, kind, piece))
        pos += len(piece)
      return self.raw_readahead_.popleft()

    # Otherwise, return the token unadultered.
    return pos, kind, text

  # Read the next raw token, but don't consume it.
  def peek_next_raw_token(self):
    try:
      pos, kind, text = self.read_next_raw_token()
      self.raw_readahead_.appendleft((pos, kind, text))
      return pos, kind, text
    except StopIteration:
      return None, None, None

  # Convert the next raw token into a full-fledged Token object, which has line
  # information. By snooping at lower-level tokens we can determine the
  # indentation level.
  def get_next(self):
    try:
      pos, kind, text = self.read_next_raw_token()
      tok = Token(pos, kind, text, self.line_, self.line_pos_)
      self.analyze_lines(tok)
      return tok
    except StopIteration:
      return None

  # Determine the line state from the new token.
  def analyze_lines(self, tok):
    if tok.kind == TokenKind.Text:
      assert (tok.text.endswith('\n') and tok.text.count('\n') <= 1) or \
             '\n' not in tok.text
    if tok.text == '\n':
      self.line_ += 1
      self.line_indent_ = ''
      self.line_pos_ = 0
      self.determined_indent_ = False
      self.first_token_on_line_ = None
      return

    if tok.kind == TokenKind.Text and tok.text.isspace() and \
       not self.determined_indent_:
      self.line_indent_ += tok.text
    elif not self.determined_indent_:
      self.determined_indent_ = True
      self.first_token_on_line_ = tok
    self.line_pos_ += len(tok.text)

  @property
  def block_type(self):
    if not self.block_type_:
      return None
    return self.block_type_[-1]

  @property
  def in_ctor_init_(self):
    return self.in_ctor_init_ is not None

  @property
  def line_indent(self):
    assert self.determined_indent_
    return self.line_indent_

  @property
  def line_pos(self):
    return self.line_pos_

# This has various transformations for tokens after a parenthetical.
class FixStyleAfterParens(PatternMatcher):
  def __init__(self, analyzer, tok):
    super(FixStyleAfterParens, self).__init__(analyzer)
    self.set_start_token(tok)

  @classmethod
  def identify(cls, analyzer, tok):
    if is_token(tok, '('):
      return cls(analyzer, tok)
    return None

  def set_start_token(self, tok):
    self.paren_level_ = 1
    self.open_paren_line_ = tok.line
    self.open_paren_indent_ = self.analyzer.line_indent
    self.close_paren_ = None
    self.open_brace_ = None
    self.open_brace_indent_ = None
    self.after_paren_tokens_ = []
    self.after_brace_tokens_ = []
    self.block_type_ = self.analyzer.block_type
    self.in_ctor_init_ = self.analyzer.in_ctor_init

  def process_token(self, tok):
    # Find where the last parenthetical was completely closed off. This is
    # used to determine whether a brace needs to be on the next line.
    if is_token(tok, '('):
      if self.paren_level_ == 0:
        self.set_start_token(tok)
      else:
        self.paren_level_ += 1
      return kProcessContinue, False

    if is_token(tok, ')'):
      self.paren_level_ -= 1
      if self.paren_level_ == 0:
        self.close_paren_ = tok
      return kProcessContinue, False

    if self.paren_level_ > 0:
      return kProcessContinue, False

    # Whitespace after the parenthetical gets ignored.
    if is_whitespace(tok):
      self.after_paren_tokens_.append(tok)
      return kProcessIgnored, False

    # Transform:
    #     (aaaaaaaaaa
    #     ) {
    #
    # Into:
    #     (aaaaaaaaaa
    #     )
    #     {
    #
    # Since clang-format has no way to conditionally place the open brace on a
    # newline, which we argue greatly improves readability (not as much for
    # 2-space indents, but definitely for 4).
    if self.should_break_parenthetical(tok):
      self.break_after_paren(tok, self.open_paren_indent_)
      return kProcessCompleted, True

    # Transform:
    #     : ida(a)
    #     , idb(b)
    #
    # Into:
    #     : ida(a),
    #       idb(b)
    #
    # Unfortunately, clang's BCIS_BeforeColon option appears broken, and has no
    # effect on binpacked items. BeforeComma works, but this style is a bit
    # ridiculous. It's designed to make moving/adding lines easier, but almost
    # no other comma-delimited list uses a convention like this, so how it ever
    # became a thing despite this inconsistency is a mystery. So, we use
    # BeforeComma, and fix it up.
    if self.should_move_comma(tok):
      self.analyzer.insert(self.close_paren_, ',')
      self.analyzer.replace(tok, ' ')
      return kProcessCompleted, True

    if self.open_brace_ is not None:
      return self.handle_after_brace(tok)

    # For constructor initializers, we always want to push the opening brace to
    # next line, since it greatly helps readability. We start deciding for that
    # here.
    if self.should_observe_next_indent(tok):
      self.open_brace_ = tok
      self.open_brace_indent_ = self.analyzer.line_indent
      return kProcessContinue, False

    return kProcessCompleted, False

  def should_break_parenthetical(self, tok):
    if not is_token(tok, '{'):
      return False
    if self.open_paren_line_ == self.close_paren_.line:
      # If the indent level is 0, this is probably a function declaration.
      # Clang does not consider this a toplevel declaration by its indent
      # level, but by its nesting level, so we account for that here.
      if not self.open_paren_indent_ and self.close_paren_.line == tok.line:
        return True
      return False
    return self.close_paren_.line == tok.line

  def should_observe_next_indent(self, tok):
    return is_token(tok, '{')

  def should_move_comma(self, tok):
    if is_token(tok, ','):
      return self.close_paren_.line + 1 == tok.line
    return False

  # Non whitespace token implies we can look at the indent level now. We're
  # concerned with two cases:
  #
  # X()
  #  : x(x) {
  #   aaaaa
  # }
  #
  # And:
  #
  # X()
  #  : x(x),
  #    y(y) {
  #   aaaaa
  # }
  #
  # In both cases, the indent is "off" from the previous line by 2.
  #
  # We also handle another odd case here:
  #
  #  X() {
  #  }
  #
  # We try to collapse this to the next line, i.e.:
  #
  #  X()
  #  {}
  #
  # But only in global scope. Otherwise we collapse to a single line if the
  # line should not exceed 100 chars:
  def handle_after_brace(self, tok):
    if is_whitespace(tok):
      self.after_brace_tokens_.append(tok)
      return kProcessIgnored, False

    empty_block = is_token(tok, '}')
    is_toplevel = (len(self.open_brace_indent_) == 0)

    if (empty_block and not is_toplevel and
        self.open_brace_indent_ == self.analyzer.line_indent_ and
        self.open_brace_.line + 1 == tok.line and
        self.open_brace_.pos < 99):
      pos, kind, text = self.analyzer.peek_next_raw_token()
      if kind == TokenKind.Text and (text == '\n' or text == '\r'):
        self.collapse_block(tok)
        return kProcessCompleted, True

    # Probably matches the constructor pattern above. Correct the indent.
    indent = self.open_brace_indent_
    if len(indent) % 2 != 0:
      indent = indent[:-1]
    else:
      indent = indent[:-2]
    self.break_after_paren(tok, indent)

    if empty_block:
      self.collapse_block(tok)

    return kProcessCompleted, True

  def break_after_paren(self, tok, indent):
      new_text = '\n' + indent
      if self.after_paren_tokens_ and self.after_paren_tokens_[0].text == ' ':
        self.analyzer.replace(self.after_paren_tokens_[0], new_text)
      else:
        self.analyzer.insert(tok, new_text)

  def collapse_block(self, tok):
    for ws_tok in self.after_brace_tokens_:
      self.analyzer.delete(ws_tok)

# Transform:
#   operator+
#
# Into:
#   operator +
class InsertSpaceAfterOperator(PatternMatcher):
  def __init__(self, analyzer, tok):
    super(InsertSpaceAfterOperator, self).__init__(analyzer)

  @classmethod
  def identify(cls, analyzer, tok):
    if tok.kind == TokenKind.Keyword and tok.text == 'operator':
      return cls(analyzer, tok)
    return None

  def process_token(self, tok):
    if tok.kind != TokenKind.Text:
      self.analyzer.insert_before(tok, ' ')
    return kProcessCompleted, True

# Transform:
#   ... {\n
#   }
#
# Into either:
#   ... {}
# Or:
#   ...\n
#   {}
class UnsplitEmptyBraces(PatternMatcher):
  def __init__(self, analyzer, tok):
    super(UnsplitEmptyBraces, self).__init__(analyzer)
    self.open_brace_ = tok
    self.state_ = 'start'
    self.indent_ = analyzer.line_indent
    self.newline_token_ = None
    self.indent_token_ = None

  @classmethod
  def identify(cls, analyzer, tok):
    if is_token(tok, '{'):
      return cls(analyzer, tok)
    return None

  def process_token(self, tok):
    if self.state_ == 'start':
      if tok.kind == TokenKind.Text and tok.text == '\n':
        self.state_ = 'got-newline'
        self.newline_token_ = tok
        return kProcessContinue, False
      return kProcessCompleted, False

    if self.state_ == 'got-newline':
      if tok.kind == TokenKind.Text and tok.text != '\n':
        self.state_ = 'waiting-for-brace'
        self.indent_token_ = tok
        return kProcessContinue, False
      return kProcessCompleted, False

    assert self.state_ == 'waiting-for-brace'
    if not is_token(tok, '}'):
      return kProcessCompleted, False

    if len(self.indent_) and self.open_brace_.col < 99:
      # This looks like an inline function, and there's space left on the line.
      # Remove the intervening newline and indentation.
      self.analyzer.delete(self.newline_token_)
      self.analyzer.delete(self.indent_token_)
    else:
      # This should almost never happen, because braces are usually moved to
      # a newline when not indented. But it can happen if the line overflows.
      self.analyzer.truncate_line_at(self.open_brace_)
      self.analyzer.insert_before(tok, '{')
    return kProcessCompleted, True

kAllPatternMatchers = [
  FixStyleAfterParens,
  InsertSpaceAfterOperator,
  UnsplitEmptyBraces,
]

if __name__ == '__main__':
  main()
