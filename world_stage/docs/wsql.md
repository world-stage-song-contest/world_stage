# WSQL

WSQL supports the same queries and data types as the [JSON search API](search-json.md).
Each query has a filter. You can add `WHERE` before the filter and `ORDER BY`
after it. WSQL does not accept SQL statements.

Keywords and field names can use upper or lower case. Strings keep their case
and accents.

```wsql
type = "entry" AND genre IN ("Rock", "Pop")
lyrics PHRASE "love forever" AND 2000 <= year < 2020
title NOT ILIKE "*remix*" WITH ACCENT
native_title IS EMPTY
date = TOMORROW ORDER BY date
@2026-01-01T00:00Z <= voting_opens < NOW
```

## Search page

Choose a page type, enter a query, then select **Search**.
The page type limits the results. You do not need to add a `type` condition.

Autocomplete suggests fields and operators for the selected page type.
It also offers known values for genres, languages, countries, years, statuses,
show types, artists, and submitters. Genres and subgenres share one list.
Year statuses and show statuses use separate lists.
Artist and submitter suggestions only include names from public search entries.
Start typing to filter the list. Suggestions work inside quotes and `IN` lists.
Selecting a value adds the quotes and escapes needed by WSQL.
Use the arrow keys to select a suggestion, then press Enter.
Press Escape to close the list. Press Ctrl+Space to open it.
Tab moves to the next control. Clear the Autocomplete checkbox to turn it off.
Search also works without JavaScript.

## Expressions

Use `AND`, `OR`, `NOT`, and parentheses to combine conditions. Comparisons apply
first, then `NOT`, then `AND`, then `OR`. Parentheses change this order.
`NOT title = "Hello"` applies `NOT` to the whole comparison.

Write `AND` between conditions that must both match. WSQL does not add it for
you. To search across text fields, use `text FUZZY "hello"`. A query cannot
contain only free text.

Outside strings, `--` normally starts a line comment. The comment ends at the
next line ending or at the end of the query. Line endings can be LF, CRLF, or CR.
A comment does not need a space before it.

The parser removes comments. You cannot recover them from the query model,
converted JSON, or canonical WSQL. Inside a string, `--` is part of the value.

```wsql
-- Entries from the 2000s
2000 <= year < 2010 -- Do not include 2010.
AND title CONTAINS "--" -- Find two hyphens.
```

`--[[` starts a block comment. `--]]` ends it. A block comment can cover more
than one line. Each marker can have 0–64 equals signs between its brackets.
For example, `--[=[` pairs with `--]=]`, and `--[==[` pairs with `--]==]`.
Both markers must have the same number of equals signs. The closing marker
must include `--`. A marker with a different count is part of the comment text.

```wsql
--[=[ This comment covers more than one line.
It can contain --[[ or --]]. --]=]
year = 2026
```

Block comments do not nest. The first matching closing marker ends the comment.
The parser removes block comments in the same way as line comments.
Inside strings, the markers are part of the value.

A missing closing marker causes an error at the opening marker.
An opening marker with more than 64 equals signs also causes an error there.
An incomplete opening marker, such as `--[=text`, starts a line comment.

| Operation | WSQL |
| --- | --- |
| Equal / not equal | `=`, `!=`, `<>` |
| Ordered comparisons | `<`, `<=`, `>`, `>=` |
| Exact text matching | `STARTS WITH`, `ENDS WITH`, `CONTAINS` |
| Wildcard matching | `LIKE`, `ILIKE` |
| Phrase / approximate text matching | `PHRASE`, `FUZZY` |
| Match a value in a list | `IN (value, value, ...)` |
| Missing value | `IS NULL`, `IS NOT NULL` |
| Missing or type's empty value | `IS EMPTY`, `IS NOT EMPTY` |

Put `NOT` before a word operator to exclude matches. Examples are `NOT IN`,
`NOT STARTS WITH`, and `NOT ILIKE`. Use `NOT (year < 2000)` to exclude matches
from an ordered comparison. Each field type supports the same operators as JSON.

You can chain ordered comparisons. The parser changes `1980 <= year < 2000` to
`1980 <= year AND year < 2000`. You can also chain equality comparisons:
`title = native_title = name`.

Do not mix `=` with ordered comparisons in a chain. Do not chain `!=` or `<>`.
Each pair of values must form a valid comparison. You can also compare two
literal values. Chains do not compare whole collections.

`ORDER BY` accepts up to five sortable fields. Separate them with commas.
Use `ASC` for ascending order or `DESC` for descending order.
The default is descending for relevance and ascending for other fields.
Without `ORDER BY`, the query uses `ORDER BY relevance DESC`.
`ORDER BY` does not accept `WITH` modifiers.

## Modifiers and aliases

A comparison or entire chain can end in a `WITH` clause:

```wsql
title CONTAINS "café" WITH CASE, NO ACCENT
native_title = "" WITH NULLS AS EMPTY
0 <= duration < 180 WITH NULLS AS EMPTY
title = native_title = name WITH CASE, ACCENT
```

Supported modifiers are `CASE`, `NO CASE`, `ACCENT`, `NO ACCENT`,
`NULLS DISTINCT`, and `NULLS AS EMPTY`. Use a modifier to change an operator's
default setting. A `WITH` clause at the end of a chain applies to every comparison
in that chain. Set each option only once per clause. For example,
`WITH CASE, NO CASE` causes an error.

The parser changes `ILIKE` to `LIKE WITH NO CASE`. You can override this setting.
For example, `ILIKE WITH CASE` is valid and formats as `LIKE`.
`IS NULL` means `= NULL`. `IS EMPTY` means `= NULL WITH NULLS AS EMPTY`.
You can override these settings too. Negative operators use a `not` expression
in the query model. They do not need separate JSON operators.

WSQL uses the same rules for NULL and missing values as JSON search.
It does not convert strings to numbers or dates. Date and time types do not
support `NULLS AS EMPTY`. You cannot set a custom value with `NULLS AS value`.

## Values and escaping

Put single-line strings in single or double quotes. Raw LF and CR line breaks
are not allowed inside quotes. Use `\n` or `\r` escapes for these characters.

For a multiline string, use `[[...]]`, `[=[...]=]`, or more equals signs.
Both markers must have the same number of equals signs, from 0 to 64.
The first matching closing marker ends the string. The markers do not nest.
Line breaks and spaces stay in the value, including a line break after the
opening marker. Comment markers inside a string are part of the value.

```wsql
lyrics CONTAINS [[First line
Second line]]
title = [[She said "don't"]]
title = [=[A value with ]] inside]=]
```

All string forms use the same escapes: `\"`, `\'`, `\\`, `\n`, `\r`, `\t`,
`\b`, and `\f`. The escapes `\[`, `\]`, and `\=` give `[`, `]`, and `=`.
An escaped bracket does not close a string. Long strings also need `\\` for
a backslash. For Unicode characters, use `\uXXXX`,
`\UXXXXXXXX`, or `\u{X}` through `\u{XXXXXX}`. Each `X` is a hexadecimal digit.

You can use a pair of `\uXXXX` escapes to write a UTF-16 surrogate pair.
The parser rejects unpaired surrogates, values above U+10FFFF, NUL, and unknown
escapes. Keywords inside quotes are strings.

```wsql
title = 'Say "Hello"'
title = "L'amour"
title CONTAINS "\u2665"
title CONTAINS "\u{1F3B5}"
```

`LIKE` and `ILIKE` match the whole value. Use `*` to match any number of
characters and `?` to match one character. `%` and `_` have no special meaning.

The parser reads string escapes before pattern escapes.
For example, `title LIKE "Love\\*"` matches `Love*` with a star at the end.
Escape the backslashes again when you put the WSQL string in a JSON request.

Write numbers without quotes. Numbers can have a sign, fraction, and exponent.
Boolean values are `TRUE` and `FALSE`. Use the special value `NULL` to test for
a missing value.

Dates and times use an `@` prefix:

```wsql
date = @2026-01-01
time = @12:30
starts_at = @2026-01-01T12:30:45+01
```

Use `YYYY-MM-DD`, `HH:MM[:SS][TZ]`, or `YYYY-MM-DDTHH:MM[:SS][TZ]`.
Seconds and timezone offsets are optional. Write offsets as `Z`, `±HH`, or
`±HH:MM`. Fractional seconds are not supported.

The current local-time field does not accept an offset. Datetimes without an
offset use the search timezone. A local time can occur twice or not occur during
a clock change. Such a datetime needs an offset, or the search returns an error.
Conversion does not check local times against a timezone.

`NOW` is a special datetime value. `TODAY`, `TOMORROW`, and `YESTERDAY` are
special date values. Conversion keeps these names. Each search uses one clock
reading to set their values. Write them without quotes or function parentheses.

## Saved queries and placeholders

Sign in to save a query. Choose a name of 1 to 100 characters.
Saved queries belong to your account. The dropdown above the editor shows their names.

- **Load** puts the query into the editor with its placeholder values filled in.
  It uses saved defaults and asks for any missing values. It does not run the query.
- **Edit** puts the original saved text into the editor. Use **Save query** to
  change its name, text, page type, placeholder types, or defaults.

These controls also work without JavaScript. The value forms then open as pages
instead of dialogs.

A placeholder is a value that starts with `$`:

```wsql
title CONTAINS $term AND $first_year <= year < $last_year
```

Names start with an ASCII letter or underscore. The other characters can be
ASCII letters, digits, or underscores. Names are case-sensitive.
Use at most 32 names per query and at most 64 characters per name.
Repeated uses of a name share one type and value.
A name inside a string or comment is not a placeholder.
Placeholders cannot replace fields, operators, or sort fields.

The save form infers each type from its comparisons. You can change any type.
The server rejects a type that does not fit the query.
If the query does not give enough information, choose a type before saving.
For example, `$first = $second` needs a type choice.
Types are text, integer, number, boolean, date, time, and datetime.
Integer is a number type that only accepts whole numbers.

Defaults are optional. An empty text value, zero, false, and NULL are valid
defaults. They are not missing values. Select **Use a default** to set a default.
Select **Use NULL** to set a NULL default instead of a text value.
Enter text without WSQL quotes. The loader adds the required quotes and escapes.
Date defaults can use TODAY, TOMORROW, or YESTERDAY. Datetime defaults can use NOW.
These names keep their meaning until the search runs.

Query names must be unique within your account. You can save up to 100 queries.
Loading keeps the saved template unchanged. Editing and saving updates it.
The normal search and conversion endpoints require all placeholders to be filled in.

## Canonical WSQL

Canonical WSQL is the standard text form of a query. It uses uppercase keywords
and lowercase field names. It keeps only the parentheses needed for grouping.
It uses `!=`, negative word operators, `ILIKE`, `IS NULL`, and `IS EMPTY` where
they apply. It leaves out default modifiers and default ordering.

The formatter combines adjacent comparisons into chains when they share an
endpoint and use the same modifiers. The operators must support chaining.
The formatter keeps condition order and operand order. It does not simplify
Boolean expressions. These changes could affect relevance scores.

Single-line strings normally use double quotes. A string with double quotes
but no single quotes uses single quotes. A string with both quote types or
with LF or CR line breaks uses long markers.

The formatter starts with `[[...]]`. It adds equals signs until the markers
can enclose the full value without ending the string too early.
This check includes a partial closing marker at the end of the value.
If none of the 0–64 counts is safe, it uses quotes and escapes instead.
With both quote types, this fallback uses double quotes.

The formatter keeps printable Unicode characters. In long strings, it also
keeps LF and CR line breaks. It escapes backslashes and other control characters.
For dates and times, it removes zero seconds. It changes zero offsets to `Z`
and `±HH:00` to `±HH`.

Parsing canonical WSQL gives the same typed query, order, and modifiers.
Formatting it again gives the same text. Different canonical text can still
have the same search results. Do not use the text as a unique key for query
meaning. Original spacing, quote choices, aliases, and comments are lost.

The JSON query limits also apply to WSQL. WSQL allows at most 65,536 characters,
4,096 tokens, and 64 syntax nesting levels. A token is one part of the query,
such as a field name, operator, or value. A request body can contain at most
64 KiB. Syntax and type errors include their location in the input.
WSQL does not support semicolons, `/* ... */` comments, custom functions, or
SQL statements.
