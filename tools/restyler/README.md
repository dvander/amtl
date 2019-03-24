restyler.py is designed as a post-processing step after running clang-format.

The desired style is basically Google-style, with some tiny differences that cannot be expressed
with clang-format alone. These differences are in line with long-standing AlliedModders convention,
some of which was derived from the no-longer-extant Mozilla style guide.

 - A brace is always placed on a newline if it follows a multi-line parenthetical. This provides a
   little extra readability. This rule includes function signatures.
 - If a function's signature is not indented (usually meaning global scope, but possibly inside a
   namespace with no indentation), then the opening brace always goes on a new line.
 - Empty functions have both braces placed after a single newline.
