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

> The full grammar is added in Task 2, together with the parser.

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
