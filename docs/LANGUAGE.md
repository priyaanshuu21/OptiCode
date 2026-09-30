# MiniC language reference

MiniC is a small, deliberately restricted C-like language. It exists so that OptiCode
can show a *complete* compiler pipeline — lexing through optimized source — without the
noise of a production language.

**Deviations from C are collected in [Deviations from C](#deviations-from-c).**

---

## 1. Lexical structure

### 1.1 Encoding and whitespace

Source is a sequence of characters. The lexer tracks 1-based line and column numbers;
a tab counts as a single column. `\n`, `\r\n`, and a lone `\r` each end one line.

Whitespace separates tokens and is otherwise discarded. It is not preserved by the
optimized-source generator.

### 1.2 Comments

| Form | Meaning |
|---|---|
| `// ...` | Line comment, ends at the next line break. |
| `/* ... */` | Block comment, may span lines. **Not nested.** An unterminated block comment is an error. |

### 1.3 Keywords (reserved)

```
int  float  void  if  else  while  for  return  break  continue  printf  scanf
```

Keywords are recognised only *after* a full identifier has been scanned, so `int1`,
`integer`, and `iffy` are ordinary identifiers.

### 1.4 Identifiers

```
ident := ( letter | '_' ) { letter | digit | '_' }
```

### 1.5 Literals

| Kind | Form | Notes |
|---|---|---|
| Integer | decimal digits | No sign, no radix prefix, no exponent. Maximum `2147483647`. |
| Float | `digits '.' digits` (a leading `.` is also accepted) | Modeled as an IEEE-754 double. |
| String | `"..."` | Escapes: `\n`, `\t`, `\"`, `\\`. Any other escape is an error. |

A string literal may not span a line; an unterminated string is an error.

### 1.6 Operators and punctuation

```
+  -  *  /  %  =  ==  !=  <  <=  >  >=  &&  ||  !  ++  --  +=  -=  *=  /=  &
(  )  {  }  ,  ;
```

`&` (address-of) is a valid token but is legal **only** in a `scanf` argument.
A lone `|` is an error. `#` produces a friendly error, since MiniC has no
preprocessor and `printf`/`scanf` are built in.

### 1.7 Maximal munch

The scanner always takes the longest valid token, so `<=` is one token and `++` is
distinct from `+ +`.

### 1.8 Errors

A lexical error is reported and the bad input is skipped; scanning continues so a
single file can produce several located diagnostics.

---

## 2. Grammar

The grammar is written in EBNF. `{ X }` is zero or more `X`, `[ X ]` is an optional `X`,
and `|` separates alternatives.

### 2.1 Program

```ebnf
program      := function { function } ;
```

A program is a non-empty sequence of function definitions. There are no globals, so an
empty program is a syntax error (`E-SYN-006`) rather than an empty compilation.

```ebnf
function     := type-name identifier "(" [ parameter { "," parameter } ] ")" block ;
parameter    := type-name identifier ;
type-name    := "int" | "float" | "void" ;
```

An empty parameter list is `()`. `void` is a return type only; `(void)` as a parameter
list is rejected.

### 2.2 Statements

```ebnf
block        := "{" { statement } "}" ;
statement    := declaration
              | assignment
              | increment
              | "if" "(" expression ")" statement [ "else" statement ]
              | "while" "(" expression ")" statement
              | "for" "(" for-init ";" [ expression ] ";" [ for-update ] ")" statement
              | "return" [ expression ] ";"
              | "break" ";"
              | "continue" ";"
              | printf-call ";"
              | scanf-call ";"
              | block
              | expression ";" ;
```

Notes:

- Any statement may appear where a statement is expected, so `if (a) if (b) g();` and a
  braced block after `if`/`while`/`for` are both legal. Braces are *not* implied.
- `declaration`, `assignment`, and `increment` are the only statements that introduce or
  change a variable, and none of them nest inside an expression.
- A bare expression statement is only legal when the expression is a **call**.
  `a;` and `1 + 2;` are rejected; `f();` and `f(a, b);` are accepted.
- A bare `;` is not a statement, so `;;` is rejected.
- `else` binds to the nearest unmatched `if`, so `else if` is just a nested `if`
  statement in the `else` position.

```ebnf
declaration  := type-name declarator { "," declarator } ";" ;
declarator   := identifier [ "=" expression ] ;

assignment   := identifier assign-op expression ";" ;
assign-op    := "=" | "+=" | "-=" | "*=" | "/=" ;

increment    := identifier ( "++" | "--" ) ";" ;

printf-call  := "printf" "(" string [ "," expression { "," expression } ] ")" ;
scanf-call   := "scanf" "(" string "," "&" identifier { "," "&" identifier } ")" ;
```

### 2.3 The `for` header

The `init` and `update` slots accept a restricted statement with the terminating `;`
supplied by the header itself:

```ebnf
for-init     := [ type-name declarator { "," declarator }
                | identifier assign-op expression
                | identifier ( "++" | "--" ) ] ;
for-update   := [ identifier assign-op expression
                | identifier ( "++" | "--" ) ] ;
```

All three parts are optional, so `for (;;) { ... }` is valid. A `for` init may declare
variables with one shared type (`for (int i = 0, j = 1; i < j; i++)`), but the update
may not.

### 2.4 Expressions

Binary operators are grouped into precedence levels, one function per level, and every
level is **left-associative**.

```ebnf
expression     := logical-or ;
logical-or     := logical-and     { "||" logical-and } ;
logical-and    := equality        { "&&" equality } ;
equality       := relational      { ( "==" | "!=" ) relational } ;
relational     := additive        { ( "<" | "<=" | ">" | ">=" ) additive } ;
additive       := multiplicative  { ( "+" | "-" ) multiplicative } ;
multiplicative := unary           { ( "*" | "/" | "%" ) unary } ;
unary          := ( "-" | "!" ) unary
               | primary ;
primary        := integer-literal
               | float-literal
               | string-literal
               | identifier [ "(" [ expression { "," expression } ] ")" ]
               | "(" expression ")" ;
```

Resulting precedence, tightest first:

| Level | Operators | Associativity |
|---|---|---|
| 7 | `!` `-` (unary) | right |
| 6 | `*` `/` `%` | left |
| 5 | `+` `-` | left |
| 4 | `<` `<=` `>` `>=` | left |
| 3 | `==` `!=` | left |
| 2 | `&&` | left |
| 1 | `\|\|` | left |

The relational level binds **tighter** than equality, matching MiniC: `a == b < c` is
`a == (b < c)`. There is no conditional (`?:`) operator and no comma operator; use
`if`/`else` and separate statements.

### 2.5 Desugaring

Two constructs are normalized during parsing so later passes only handle one form. The
`Assign.sugar` attribute records what the source actually said, which lets the unparser
re-emit the original text.

| Source | Stored as |
|---|---|
| `a += e` | `a = a + e` with `sugar = "+="` |
| `a -= e` | `a = a - e` with `sugar = "-="` |
| `a *= e` | `a = a * e` with `sugar = "*="` |
| `a /= e` | `a = a / e` with `sugar = "/="` |
| `a++` | `a = a + 1` with `sugar = "++"` |
| `a--` | `a = a - 1` with `sugar = "--"` |
| `int a = 1, b = 2;` | two `VarDecl` nodes, each with its own `init` |

Desugared nodes are ordinary AST nodes: they get spans, they get ids, and the optimizer
may rewrite them like any other node. An assignment target is stored as a plain name
string on `Assign`, not as an `Identifier` node.

### 2.6 Error recovery

The parser reports at most 50 diagnostics per run and never raises. On an unexpected
token it records the error, resynchronizes at the next statement boundary, and keeps
building the rest of the tree, so the API still returns the partial AST for everything
that did parse. Nesting deeper than 50 blocks or parenthesized expressions is reported
as `E-SYN-005` rather than exhausting the stack, and exceeding the diagnostic budget
adds a final `E-SYN-008`.

## 3. Execution semantics

> Filled in once the TAC interpreter exists (Task 4).

## 4. Deviations from C

- No preprocessor, `#include`, macros, or conditional compilation.
- No pointers, arrays, structs, unions, enums, `typedef`, casts, or `char`/`double`.
- No `switch`, `do`, `goto`, or `switch`-style fallthrough.
- No globals: every program is a sequence of function definitions.
- Assignment is a **statement**, not an expression, so `a = b = c` is invalid.
- `++`/`--` are **statements**, not expressions.
- `printf` and `scanf` are reserved words with fixed behaviour rather than library functions.
- Integer literals are decimal only, so `INT_MIN` cannot be written literally; use `-2147483647 - 1`.
- Shadowing a visible variable is an error.
