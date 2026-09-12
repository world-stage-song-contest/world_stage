# Search API

The search API accepts JSON query objects and WSQL strings. JSON queries include
a version number. Both formats use the same query model, type checks, and search
code. There is no free-text search syntax or visual query builder yet.

Use `POST /api/search` to run a query. Use `GET /api/search/schema` to get the
available fields and types. Use `POST /api/search/convert` to convert between
formats. Conversion does not run a search or access the database.

All three endpoints are public. Successful responses put their data in a
`result` object. Signing in does not give access to more search data.

## WSQL requests and conversion

The `query` property accepts a WSQL string or the JSON object described below.
Both formats use the same page controls and response format.
`result.query` always contains normalized JSON. This form includes default
settings and replaces syntax shortcuts with basic query operations.

```json
{
  "query": "type = \"entry\" AND lyrics CONTAINS \"love\" AND 1980 <= year < 2000 ORDER BY year DESC",
  "page": {"limit": 50, "offset": 0}
}
```

Convert WSQL to normalized JSON:

```http
POST /api/search/convert
Content-Type: application/json

{"query": "title NOT ILIKE 'Love*'", "to": "json"}
```

Convert JSON to canonical WSQL:

```json
{
  "query": {
    "version": 1,
    "where": {
      "kind": "comparison",
      "operator": "equals",
      "left": {"kind": "field", "name": "year"},
      "right": {"kind": "number", "value": 2026}
    }
  },
  "to": "wsql"
}
```

The conversion response is `{"result":{"format":"wsql","query":"year = 2026"}}`.
For `to: "json"`, `result.query` is an object. The API checks the type of `query`
to select the input format. You can convert to the same format to get normalized
JSON or canonical WSQL. Both directions reject invalid types, operators, and
modifiers.

See [WSQL syntax](wsql.md) for operators, escapes, modifiers, and canonical formatting.

## Request

```json
{
  "query": {
    "version": 1,
    "where": {
      "kind": "and",
      "operands": [
        {
          "kind": "comparison",
          "operator": "equals",
          "left": {"kind": "field", "name": "type"},
          "right": {"kind": "string", "value": "entry"}
        },
        {
          "kind": "comparison",
          "operator": "contains",
          "left": {"kind": "field", "name": "lyrics"},
          "right": {"kind": "string", "value": "love"}
        },
        {
          "kind": "comparison",
          "operator": "greater_than_or_equal",
          "left": {"kind": "field", "name": "year"},
          "right": {"kind": "number", "value": 1980}
        }
      ]
    },
    "orderBy": [
      {
        "expression": {"kind": "field", "name": "relevance"},
        "direction": "descending"
      },
      {
        "expression": {"kind": "field", "name": "year"},
        "direction": "descending"
      }
    ]
  },
  "page": {"limit": 50, "offset": 0}
}
```

`where` is required. The default order is relevance from highest to lowest.
The default page has 50 results and starts at offset zero.
The API rejects unknown properties.

## Expressions and values

Expression kinds are `comparison`, `and`, `or`, and `not`.
The `and` and `or` groups have an `operands` array. A `not` expression has one
`operand`. Use `not` to exclude a match, including an equality or list match.
Write a chain as an `and` group with one comparison for each adjacent pair.

```json
{
  "kind": "not",
  "operand": {
    "kind": "comparison",
    "operator": "like",
    "left": {"kind": "field", "name": "title"},
    "right": {"kind": "pattern", "value": "*Remix"},
    "modifiers": {"case": "insensitive"}
  }
}
```

The query model has no `ilike`, `not_like`, or `not_equals` operators.
The WSQL parser changes these forms to the operations above.

Single values use `kind` and `value`. Their kinds are `string`, `number`,
`boolean`, `date`, `time`, and `datetime`. Write null as `{"kind":"null"}`.
Strings use standard JSON escapes and contain decoded Unicode.
The API does not apply WSQL string escapes to JSON query objects.
It rejects NUL and unpaired Unicode surrogates.

To match a value in a list, use `in` with this form on the right:

```json
{
  "kind": "list",
  "value": [
    {"kind": "string", "value": "Rock"},
    {"kind": "string", "value": "Synthpop"},
    {"kind": "null"}
  ]
}
```

You can compare literal values. You can also compare two fields
if each has a single value. Their types must match, and they must share a page
category. The API does not convert numbers or booleans to strings.
Use a date value to compare dates.

## Types and modifiers

Operators belong to types. The schema endpoint lists fields separately.
Each field states its type and result categories. It also states whether the
field has multiple values and whether you can sort by it.
You can use `relevance` only to sort results.

| Type | Positive operators |
| --- | --- |
| Text | `equals`, `in`, `starts_with`, `ends_with`, `contains`, `like`, `phrase`, `fuzzy` |
| Number, date, time, datetime | `equals`, `in`, `less_than`, `less_than_or_equal`, `greater_than`, `greater_than_or_equal` |
| Boolean | `equals`, `in` |

Set `nulls` to `"distinct"` or `"as_empty"`. Text comparisons also accept `case`
and `accent`. Set each to `"sensitive"` or `"insensitive"`.
The defaults keep nulls distinct and ignore case and accents.
The `like` operator differs: it is case-sensitive by default.
Use modifiers to change these defaults. Date and time types have no empty value
and do not support `as_empty`.

A missing value does not match a comparison against a non-null value.
This rule also applies under `not`. Compare with the special value null to find
missing values. With `as_empty`, both missing values and the null value become
`""`, `0`, or `false`, based on the type. Text that contains only spaces or other
whitespace is not empty.

Text fields such as `genre`, `artist`, and `lyrics` can have multiple values.
The field matches if any value matches. Under `not`, any matching value excludes
the whole page. A missing collection equals null. With `as_empty`, a missing
collection acts as an empty string. This version does not compare a collection
to another field or accept whole collections as values.

A field cannot match a page category that does not have that field.
This rule also applies under `not`, a null test, or `as_empty`.
For example, `native_title = NULL` finds entries with no native title.
It does not return countries or years.

## Text matching

- Equality, prefix, suffix, and substring comparisons give punctuation no special meaning.
- `like` matches the whole value. `*` matches any number of characters.
  `?` matches one character. A backslash escapes the next pattern character.
  To match a star, write `"\\*"` in JSON. `%` and `_` have no special meaning.
- `phrase` matches words next to each other in one value. It treats runs of
  whitespace as one space. A lyric phrase can cross a line break.
  It cannot cross from one translation or field to another.
- `fuzzy` checks for exact, prefix, and substring matches first.
  With at least three query characters, it also allows a limited number of edits.
  An edit adds, removes, or changes one character. It compares whole values and
  words split at whitespace. The limit is 30% of query length, rounded up, with
  at most three edits. Fuzzy input can have at most 128 characters.
  With fewer than three characters, only exact, prefix, and substring matches
  apply. Case and accent settings apply before matching.

The `text` field searches a fixed set of values for each page category.
Entry text includes titles, artist credits, artist names, country names, and
country codes. It also includes historical country names that apply to the entry.
Other values are year and special names, languages, genre labels, submitter,
and all three lyric versions. Search uses the genre labels shown on entry pages.
It does not add names from the parent genre table.

Artist text includes the current main and native names. It also includes stage
names used by current entries. Country text includes names, codes, and other
names. Submitter text contains the username. Year text contains its label,
special short name, and host country. Show text contains its full display name,
short name, and date. Search does not include notes, sources, messages, or
account credentials.

Positive text matches add to the relevance score. Names and titles have more
weight than other details and lyrics. AND adds scores. OR uses the highest score.
NOT adds zero. Relevance sets the result order. It is not a probability.
Exact filters on structural fields add no score. Results with equal sort values
use the result type and ID to set their order. Missing sort values come last.

## Date and time values

Examples are `2026-01-01`, `12:30`, and `2026-01-01T12:30+01`.
Do not put `@` before a JSON date or time value. Normalized values leave out
zero seconds. Zero offsets become `Z`, and `±HH:00` becomes `±HH`.
The API rejects fractional seconds and other calendar formats.

Use `{"kind":"temporal_sentinel","name":"today"}` for a value based on the
search clock. The names `today`, `tomorrow`, and `yesterday` give dates.
The name `now` gives a datetime. Each search uses one clock reading for all
these values. The normalized response keeps the name instead of the date or time.

Set `SEARCH_TIMEZONE` to change the search timezone.
The default is `Europe/Warsaw`. This setting controls date values based on the
clock and the show's `date` and `time` fields. It also applies to datetimes
without offsets. A local time can occur twice or not occur during a clock change.
Such a datetime needs an offset. Dates and datetimes are separate types.
Use `starts_at` for the show's scheduled datetime. The `time` field uses local
time and does not accept an offset.

## Results and visibility

The page categories are `entry`, `year`, `country`, `submitter`, `artist`, and
`show`. Specials use the `year` category and keep their negative year IDs.
Use `special` to distinguish specials from regular years.

Search uses only the latest song data. It excludes placeholders.
It includes regular entries, special entries, and national-final candidate pages.
Pending submissions are already visible on public entry pages, so search
includes them too. All callers can search the same data.

Country pages need a main entry in their history. Submitter and artist pages
need an entry in their history. Shows need a visible lineup and a public state
of `draw`, `partial`, or `full`. Search excludes national-final stage pages.
Placement is available only for closed years. You cannot search private ballots,
hidden points, or moderation data through these fields.

A successful response contains:

- `result.query`: normalized JSON with default modifiers and ordering filled in.
- `result.results`: objects with `type`, string `id`, `title`, page `url`,
  numeric `relevance`, and selected `data` for the page category.
- `result.page`: `limit`, `offset`, and `nextOffset`, which is null on the last page.
- `result.context`: timezone and the search clock reading in `evaluatedAt`.

Page offsets stay stable while the data and clock stay unchanged.
This version has no saved result snapshots, total counts, matching text excerpts,
or query fingerprints. A fingerprint is a key used to identify a query.
PostgreSQL sorts the results and selects the requested page.

## Value suggestions

Use `GET /api/search/choices?type=entry&field=genre` to get value suggestions.
The endpoint is public. Both `type` and `field` are required.
An unsupported field or page type returns HTTP 400.

The `result` list contains objects with `value` and `literal`.
`value` is the database value. `literal` is ready to insert into WSQL.
Text literals include the required quotes and escapes. Years are numbers.

Supported fields are `type`, `genre`, `language`, `country`, `code`, `year`,
`status`, `show_type`, `artist`, and `submitter`.
The field must be available for the selected page type.
`status` uses the year status list or show status list as needed.
Reference lists include unused values. Artists and submitters only include
names from entries visible to public search.

These values are suggestions, not new validation rules.
Search still accepts partial text and other valid WSQL values.

## Saved query API

Saved query endpoints require a signed-in session or an API token.
Each endpoint only exposes the caller's own queries.
An unknown query ID or another user's query ID returns HTTP 404.

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/search/placeholders` | Infer placeholder types from WSQL. No sign-in is required. |
| GET | `/api/search/saved` | List saved query IDs, names, and page types. |
| POST | `/api/search/saved` | Create a saved query. |
| GET | `/api/search/saved/<id>` | Read its original text and settings. |
| PUT | `/api/search/saved/<id>` | Replace its name, text, page type, and settings. |
| POST | `/api/search/saved/<id>/resolve` | Fill in values without running the query. |

To infer types, send `{"query": "year >= $start"}`. An optional `types` object
maps names to manual type choices. The result lists each name, its `type`, and
its `inferredType`. An unknown type is null and needs a choice before saving.

To create or update a saved query, send:

```json
{
  "name": "Recent titles",
  "type": "entry",
  "query": "year >= $start AND title CONTAINS $term",
  "parameters": {
    "start": {"type": "integer", "default": 2020},
    "term": {"type": "text"}
  }
}
```

The name limit is 100 characters. Parameter types can be inferred when omitted.
Manual types must fit the query. Defaults use JSON values. Date and time defaults
use strings without the WSQL `@` prefix. Omit `default` to require a value on load.
An explicit null default is a NULL value, not a missing default.

To resolve the example, send `{"values": {"term": "love"}}`.
Values override defaults. The result contains `query`, `type`, and an empty
`missing` list when all values are present. Otherwise, `missing` lists the names
and types that still need values. Read responses keep `query_text`, `result_type`,
and `parameters`. List, create, and update responses use `id`, `name`, and `result_type`.
All of these API responses put their data inside `result`.

## Limits and implementation

The limits are:

- 64 KiB per request body.
- 100 expression nodes and 16 nesting levels.
- 256 comparisons after expanding IN lists.
- 100 values per IN list.
- Five sort fields.
- 64 wildcards per pattern.
- 4,096 characters per string.
- 1–100 results per page.
- Page offsets from 0 to 10,000.

The database stops a query after five seconds.

Invalid queries return HTTP 400 with `error.code` and `error.description`.
When available, `error.path` gives a JSON path within the query.
WSQL errors also include `error.location` with `offset`, `length`, `line`, and
`column`. Offsets start at zero. Lines and columns start at one.
Locations count Unicode code points in the decoded WSQL string.
They do not count UTF-8 bytes or JavaScript UTF-16 code units.
Queries that exceed the time limit return HTTP 503 with `search_timeout`.
Requests that exceed the size limit return HTTP 413.

`decode_query`, `encode_query`, `parse_wsql`, `format_wsql`, `parse_search_query`,
`convert_query`, `compile_search`, and `execute_search` are shared Python
functions. API handlers and future page handlers can call them directly.
SQL names and search expressions come from fixed server definitions.
The code passes query values as bound parameters, separate from SQL text.
It parses WSQL before building SQL. It never runs WSQL input as SQL text.
