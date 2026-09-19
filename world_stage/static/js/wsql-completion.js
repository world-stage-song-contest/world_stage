const spellings = {
    equals: ['=', '!=', '<>'],
    in: ['IN', 'NOT IN'],
    less_than: ['<'],
    less_than_or_equal: ['<='],
    greater_than: ['>'],
    greater_than_or_equal: ['>='],
    starts_with: ['STARTS WITH', 'NOT STARTS WITH'],
    ends_with: ['ENDS WITH', 'NOT ENDS WITH'],
    contains: ['CONTAINS', 'NOT CONTAINS'],
    like: ['LIKE', 'ILIKE', 'NOT LIKE', 'NOT ILIKE'],
    phrase: ['PHRASE', 'NOT PHRASE'],
    fuzzy: ['FUZZY', 'NOT FUZZY'],
};

function tokensBefore(source, caret) {
    const tokens = [];
    let position = 0;
    while (position < caret) {
        if (tokens.length > 4096) return null;
        const tail = source.slice(position, caret);
        const whitespace = /^\s+/.exec(tail);
        if (whitespace) {
            position += whitespace[0].length;
            continue;
        }
        const comment = tail.startsWith('--');
        const opening = /^(?:--)?\[(=*)\[/.exec(tail);
        if (comment && !opening) {
            const end = tail.search(/[\r\n]/);
            if (end < 0) return null;
            position += end;
            continue;
        }
        if (opening || tail[0] === '"' || tail[0] === "'") {
            if (opening && opening[1].length > 64) return null;
            const start = position;
            const closing = opening ? `${comment ? '--' : ''}]${opening[1]}]` : tail[0];
            position += opening ? opening[0].length : 1;
            const contentStart = position;
            let closed = false;
            while (position < caret) {
                if (source.startsWith(closing, position) && position + closing.length <= caret) {
                    position += closing.length;
                    closed = true;
                    break;
                }
                if (!comment && source[position] === '\\') position++;
                position++;
            }
            if (!closed && comment) return null;
            if (!comment) tokens.push({text: '', kind: 'value', start, end: Math.min(position, caret),
                string: true, closed, contentStart, closing});
            continue;
        }
        const match = /^(?:@(?:(?!--)[\w:+-])+|\$[A-Za-z_][A-Za-z_0-9]*|[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?|[A-Za-z_][A-Za-z_0-9]*|!=|<>|<=|>=|[=<>(),])/.exec(tail);
        if (!match) return null;
        const text = match[0];
        const kind = /^[A-Za-z_]/.test(text) ? 'word' : /^[=<>!(),]/.test(text) ? 'symbol' : 'value';
        tokens.push({text: text.toUpperCase(), kind, start: position, end: position + text.length});
        position += text.length;
    }
    return tokens;
}

function folded(value) {
    return String(value).normalize('NFD').replace(/\p{M}/gu, '').toLocaleLowerCase();
}

function stringPrefix(source) {
    let value = '';
    const escapes = {'n': '\n', 'r': '\r', 't': '\t', 'b': '\b', 'f': '\f',
        '"': '"', "'": "'", '\\': '\\', '[': '[', ']': ']', '=': '='};
    for (let i = 0; i < source.length; i++) {
        if (source[i] !== '\\') {
            value += source[i];
            continue;
        }
        const escape = source[++i];
        if (Object.hasOwn(escapes, escape)) value += escapes[escape];
        else {
            const unicode = /^(?:u\{([\da-fA-F]{1,6})\}|u([\da-fA-F]{4})|U([\da-fA-F]{8}))/.exec(source.slice(i));
            if (!unicode) return null;
            const code = parseInt(unicode[1] ?? unicode[2] ?? unicode[3], 16);
            if (code > 0x10ffff) return null;
            value += String.fromCodePoint(code);
            i += unicode[0].length - 1;
        }
    }
    return value;
}

function offerChoices(source, caret, tokens, from, choices) {
    const empty = {start: caret, end: caret, suggestions: []};
    const rest = tokens.slice(from);
    if (rest.length > 1) return empty;
    const token = rest[0];
    if (token?.string && token.closed) return empty;
    if (token && !token.string && token.kind !== 'word' && !/^[+-]?\d/.test(token.text)) return empty;
    let prefix = token ? source.slice(token.start, caret).trimEnd() : '';
    let end = caret + (/^[A-Za-z_0-9]*/.exec(source.slice(caret))[0].length);
    if (token?.string) {
        prefix = stringPrefix(source.slice(token.contentStart, caret));
        if (prefix === null) return empty;
        end = caret;
        for (let position = caret; position < source.length; position++) {
            if (source.startsWith(token.closing, position)) {
                end = position + token.closing.length;
                break;
            }
            if (source[position] === '\\') position++;
            else if (token.closing.length === 1 && /[\r\n]/.test(source[position])) break;
        }
    }
    return {start: token?.start ?? caret, end,
        suggestions: choices.filter(choice => folded(choice.value).startsWith(folded(prefix)))
            .map(choice => choice.literal)};
}

export function completeWsql(source, caret, schema, resultType, choices = {}) {
    const empty = {start: caret, end: caret, suggestions: []};
    if (!schema.resultTypes.includes(resultType)) return empty;
    const tokens = tokensBefore(source, caret);
    if (!tokens) return empty;
    const fields = Object.entries(schema.fields).filter(([, field]) => field.resultTypes.includes(resultType));
    const filterFields = fields.filter(([name]) => name !== 'relevance').map(([name]) => name);
    const sortFields = fields.filter(([, field]) => field.sortable).map(([name]) => name);
    const words = tokens.map(token => token.text);
    const endsWithSpace = /\s$/.test(source.slice(0, caret));

    function offer(candidates, from) {
        const rest = tokens.slice(from);
        if (rest.length && source.slice(rest.at(-1).end, caret).trim()) return empty;
        if (rest.some((token, index) => token.kind === 'value' ||
            (index > 0 && source.slice(rest[index - 1].end, token.start).trim()))) return empty;
        const prefix = rest.map(token => token.text).join(' ');
        const suggestions = [...new Set(candidates)].filter(candidate => {
            const upper = candidate.toUpperCase();
            return upper.startsWith(prefix) && !(upper === prefix && endsWithSpace);
        });
        const start = rest[0]?.start ?? caret;
        const suffix = /^[A-Za-z_0-9]*/.exec(source.slice(caret))[0];
        return {start, end: caret + suffix.length, suggestions};
    }

    const order = words.lastIndexOf('ORDER');
    if (order >= 0) {
        if (words[order + 1] !== 'BY') return offer(['ORDER BY'], order);
        let from = order + 2;
        for (let i = from; i < words.length; i++) if (words[i] === ',') from = i + 1;
        if (tokens.length === from || !sortFields.includes(words[from]?.toLowerCase()) ||
            (tokens.length === from + 1 && !endsWithSpace)) return offer(sortFields, from);
        return offer(['ASC', 'DESC'], from + 1);
    }

    let from = 0;
    for (let i = 0; i < tokens.length; i++) {
        if (['AND', 'OR', 'WHERE'].includes(words[i])) from = i + 1;
    }
    while (['(', 'NOT'].includes(words[from])) from++;
    if (tokens.length === from) return offer(filterFields, from);
    const fieldName = words[from]?.toLowerCase();
    if (!filterFields.includes(fieldName) || (tokens.length === from + 1 && !endsWithSpace)) {
        return offer([...filterFields, 'NOT', ...(from === 0 ? ['WHERE'] : [])], from);
    }
    const definition = schema.types[schema.fields[fieldName].type];
    const operators = definition.operators.flatMap(operator => spellings[operator] ?? []);
    operators.push('IS NULL', 'IS NOT NULL');
    if (definition.supportsEmpty) operators.push('IS EMPTY', 'IS NOT EMPTY');
    const operatorSuggestions = offer(operators, from + 1);
    if (operatorSuggestions.suggestions.length) return operatorSuggestions;
    const operator = operators.sort((a, b) => b.length - a.length).find(candidate => {
        const parts = candidate.split(' ');
        return parts.every((part, index) => words[from + 1 + index] === part);
    });
    if (!operator) return empty;
    let valueStart = from + 1 + operator.split(' ').length;
    if (words[valueStart] === '(') valueStart++;
    if (operator.endsWith('IN')) {
        for (let i = valueStart; i < words.length; i++) if (words[i] === ',') valueStart = i + 1;
    }
    const values = ['NULL'];
    if (schema.fields[fieldName].type === 'boolean') values.push('TRUE', 'FALSE');
    if (schema.fields[fieldName].type === 'date') values.push('TODAY', 'TOMORROW', 'YESTERDAY');
    if (schema.fields[fieldName].type === 'datetime') values.push('NOW');
    if (!operator.endsWith('IN')) {
        values.push(...fields.filter(([name, field]) => name !== 'relevance' && field.type === schema.fields[fieldName].type).map(([name]) => name));
    }
    if (!operator.startsWith('IS ')) {
        const rest = tokens.slice(valueStart);
        const valuePosition = rest.length === 0 || (rest.length === 1 &&
            (rest[0].string ? !rest[0].closed : !endsWithSpace));
        if (valuePosition) {
            const options = offerChoices(source, caret, tokens, valueStart, choices[fieldName] ?? []);
            const generic = rest[0]?.string ? empty : offer(values, valueStart);
            return {...options, valueField: fieldName,
                suggestions: [...new Set([...options.suggestions, ...generic.suggestions])]};
        }
        const valueSuggestions = offer(values, valueStart);
        if (valueSuggestions.suggestions.length) return valueSuggestions;
        if (!tokens[valueStart]) return empty;
    }
    const after = operator.startsWith('IS ') ? valueStart : valueStart + 1;
    const modifier = words.indexOf('WITH', after);
    if (modifier >= 0) {
        const modifiers = ['NULLS DISTINCT'];
        if (definition.supportsEmpty) modifiers.push('NULLS AS EMPTY');
        if (definition.modifiers.includes('case')) modifiers.push('CASE', 'NO CASE');
        if (definition.modifiers.includes('accent')) modifiers.push('ACCENT', 'NO ACCENT');
        const start = Math.max(modifier, words.lastIndexOf(',')) + 1;
        const suggestions = offer(modifiers, start);
        if (suggestions.suggestions.length) return suggestions;
        return offer(['AND', 'OR', 'ORDER BY'], tokens.length - (endsWithSpace ? 0 : 1));
    }
    let next = after;
    while (words[next] === ')') next++;
    return offer(['AND', 'OR', 'ORDER BY', 'WITH'], next);
}
