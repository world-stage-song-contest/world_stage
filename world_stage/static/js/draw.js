// The following variables are provided in the html file this is included in
// multiDraw: boolean
// year: number

class Xoshiro256StarStar {
    constructor(seed) {
        function splitmix64(seed) {
            let z = BigInt.asUintN(64, BigInt(seed));
            return function () {
                z = (z + 0x9e3779b97f4a7c15n) & 0xffffffffffffffffn;
                let r = z;
                r = (r ^ (r >> 30n)) * 0xbf58476d1ce4e5b9n & 0xffffffffffffffffn;
                r = (r ^ (r >> 27n)) * 0x94d049bb133111ebn & 0xffffffffffffffffn;
                return r ^ (r >> 31n);
            };
        }

        const sm64 = splitmix64(BigInt(seed));
        this.s = [sm64(), sm64(), sm64(), sm64()];
    }

    /**
     * @returns {bigint}
     */
    next64 = () => {
        // xoshiro256** algorithm
        let [s0, s1, s2, s3] = this.s;
        const result = BigInt.asUintN(64, ((s1 * 5n) << 7n | 0n) * 9n);

        const t = BigInt.asUintN(64, s1 << 17n);

        s2 ^= s0;
        s3 ^= s1;
        s1 ^= s2;
        s0 ^= s3;

        s2 ^= t;
        s3 = (s3 << 45n | s3 >> (64n - 45n)) & 0xffffffffffffffffn;

        this.s = [s0, s1, s2, s3];
        return result & 0xffffffffffffffffn;
    }

    /**
     * @returns {number}
     */
    next32 = () => {
        return Number(this.next64() >> 32n) >>> 0;
    }

    /**
     * @returns {number}
     */
    nextFloat = () => {
        // Uses highest 53 bits for IEEE-754 double
        const v = this.next64() >> 11n;
        return Number(v) / 0x1_0000_0000_0000_00;
    }

    /**
     * @param {number} limit
     * @returns {number}
     */
    next = limit => {
        return this.next32() % limit;
    }

    /**
     * @template T
     * @param {T[]} a
     * @returns {T[]}
     */
    shuffle = a => {
        for (let i = a.length - 1; i > 0; --i) {
            const j = this.next(i + 1);
            [a[i], a[j]] = [a[j], a[i]];
        }
        return a;
    }

    /**
     * @template T
     * @param {T[]} a
     * @returns {T}
     */
    select = a => {
        return a[this.next(a.length)];
    }

    /**
     * @template T
     * @param {T[]} a
     * @returns {T}
     */
    pop = a => {
        const i = this.next(a.length);
        return a.splice(i, 1)[0];
    }
}

function toggleHeader() {
    const header = document.querySelector("header");
    header.classList.toggle("hidden");
}

/**
 * @param {number} t
 * @returns {number}
 */
function easeOutCubic(t) {
    return 1 - Math.pow(1 - t, 3);
}

async function save() {
    const error = document.querySelector(".error");
    const data = {};
    for (const show of document.querySelectorAll('.show')) {
        const ro = [];
        for (const slot of show.querySelectorAll(".show-country")) {
            // Skip empty slots (e.g. an aborted draw); only assigned slots
            // carry a song id.
            if (!slot.dataset.id) continue;
            ro.push({ id: parseInt(slot.dataset.id, 10), ro: parseInt(slot.dataset.index, 10) });
        }
        ro.sort((a, b) => a.ro - b.ro);
        data[show.dataset.name] = ro.map(e => e.id);
    }
    const res = await fetch(window.location.pathname, {
        method: "POST",
        headers: {
            'Accept': 'application/json',
            'Content-Type': 'application/json'
        },
        body: JSON.stringify(data)
    })

    const body = await res.json();
    if (!res.ok) {
        error.classList.remove("hidden");
        error.textContent = body.error;
    } else {
        error.classList.add("hidden");
    }
}

class Entry {
    /** @type {string} */
    id;
    /** @type {string} */
    code;
    /** @type {string} */
    genre;
    /** @type {string} */
    language;
    /** @type {number} */
    submitter;
    /** @type {number} */
    pot;
    /** @type {Element} */
    element;

    /**
     * @param {Element} element
     * @param {number} i
     */
    constructor(element, i) {
        this.id = element.dataset.id;
        this.code = element.dataset.code;
        // Empty string means "untagged" — entries without a genre or
        // language don't contribute to that balance dimension.
        this.genre = element.dataset.genre || '';
        this.language = element.dataset.language || '';
        this.submitter = parseInt(element.dataset.submitter);
        this.pot = i;
        this.element = element;
    }
}

// Soft-balance keys: each is a per-entry tag that the show-distribution
// balancer caps at ⌈total / N⌉ per show, and that the running-order
// spreader uses to detect adjacency conflicts.
const BALANCE_KEYS = ['genre', 'language'];

/**
 * Validates that the allocation problem has a feasible solution
 * @param {Entry[][]} pots - Array of pots, each containing entries
 * @param {Object<string, {limit: number, entries: Entry[]}>} shows - Show configurations
 * @throws {Error} If validation fails
 */
function validateAllocation(pots, shows) {
    const showNames = Object.keys(shows);
    if (showNames.length < 2) {
        throw new Error('Need at least 2 shows for multi-draw allocation');
    }

    const totalEntries = pots.reduce((sum, pot) => sum + pot.length, 0);
    const totalCapacity = showNames.reduce((sum, name) => sum + shows[name].limit, 0);

    if (totalEntries > totalCapacity) {
        throw new Error(`Allocation impossible: ${totalEntries} entries exceed ${totalCapacity} available slots`);
    }

    // Check submitter constraints
    const entriesPerSubmitter = new Map();
    for (const pot of pots) {
        for (const entry of pot) {
            entriesPerSubmitter.set(entry.submitter, (entriesPerSubmitter.get(entry.submitter) ?? 0) + 1);
        }
    }

    for (const [submitter, count] of entriesPerSubmitter) {
        if (count > showNames.length) {
            throw new Error(`Submitter ${submitter} has ${count} entries but only ${showNames.length} shows available`);
        }
    }
}

/**
 * Assigns entries from a single pot to available shows using bipartite matching
 * @param {Entry[]} pot - Entries to assign (will be modified)
 * @param {Array<{name: string, limit: number, entries: Entry[], submitters: Set<number>}>} liveShows - Shows with capacity
 * @param {Xoshiro256StarStar} rng - Random number generator
 * @returns {boolean} True if assignment succeeded
 */
/**
 * Per-key ceiling: ⌈entries-of-key / nShows⌉. Used to enforce balanced
 * spread of countries / genres across shows during bipartite matching.
 */
function ceilingByKey(pot, key, nShows) {
    const counts = new Map();
    for (const e of pot) {
        const v = e[key];
        if (!v) continue;
        counts.set(v, (counts.get(v) || 0) + 1);
    }
    const ceil = new Map();
    for (const [k, c] of counts) ceil.set(k, Math.ceil(c / nShows));
    return ceil;
}

function assignPotToShows(pot, liveShows, rng, balanceCeils = null) {
    const showCount = liveShows.length;
    const entryCount = pot.length;

    // Per-key ceiling caps each show to ⌈total_of_this_value / N⌉
    // entries for each balance dimension (genre, language). Computed
    // once globally across all pots + leftovers so phase 1 and phase 2
    // share targets — falls back to per-pot computation if the caller
    // didn't supply one.
    if (balanceCeils === null) {
        balanceCeils = {};
        for (const key of BALANCE_KEYS) {
            balanceCeils[key] = ceilingByKey(pot, key, showCount);
        }
    }

    // Build adjacency lists (show -> compatible entries). For specials,
    // the same country can submit multiple entries; spread them across
    // shows by also excluding entries whose country is already in the
    // show. The leftovers phase relaxes this constraint, so a country
    // with more entries than shows still gets placed — just balanced.
    const adjacency = Array.from({ length: showCount }, () => []);
    for (let showIndex = 0; showIndex < showCount; ++showIndex) {
        const show = liveShows[showIndex];
        for (let entryIndex = 0; entryIndex < entryCount; ++entryIndex) {
            const entry = pot[entryIndex];
            if (show.submitters.has(entry.submitter)) continue;
            if (show.codes && show.codes.has(entry.code)) continue;
            let blocked = false;
            for (const key of BALANCE_KEYS) {
                const v = entry[key];
                if (!v) continue;
                const cap = balanceCeils[key]?.get(v);
                if (cap !== undefined
                    && (show.balanceCounts[key].get(v) || 0) >= cap) {
                    blocked = true;
                    break;
                }
            }
            if (blocked) continue;
            adjacency[showIndex].push(entryIndex);
        }
    }

    // Find maximum bipartite matching (entry -> show)
    const matchEntry = Array(entryCount).fill(-1);
    const seen = Array(entryCount);

    /**
     * DFS to find augmenting path
     * @param {number} showIndex
     * @returns {boolean}
     */
    function findAugmentingPath(showIndex) {
        for (const entryIndex of adjacency[showIndex]) {
            if (seen[entryIndex]) continue;
            seen[entryIndex] = true;

            if (matchEntry[entryIndex] === -1 || findAugmentingPath(matchEntry[entryIndex])) {
                matchEntry[entryIndex] = showIndex;
                return true;
            }
        }
        return false;
    }

    // Try to match each show
    for (let showIndex = 0; showIndex < showCount; ++showIndex) {
        seen.fill(false);
        findAugmentingPath(showIndex);
    }

    // Apply the matching and collect assigned indices
    const assignedIndices = [];
    for (let entryIndex = 0; entryIndex < entryCount; ++entryIndex) {
        const showIndex = matchEntry[entryIndex];
        if (showIndex !== -1) {
            const show = liveShows[showIndex];
            const entry = pot[entryIndex];
            show.entries.push(entry);
            show.submitters.add(entry.submitter);
            if (show.codes) show.codes.add(entry.code);
            if (show.balanceCounts) {
                for (const key of BALANCE_KEYS) {
                    const v = entry[key];
                    if (!v) continue;
                    show.balanceCounts[key].set(
                        v,
                        (show.balanceCounts[key].get(v) || 0) + 1,
                    );
                }
            }
            assignedIndices.push(entryIndex);
        }
    }

    // Remove assigned entries efficiently
    const remainingEntries = pot.filter((_, index) => !assignedIndices.includes(index));
    pot.length = 0;
    pot.push(...remainingEntries);

    return true;
}

/**
 * Assigns leftover entries to remaining show slots
 * @param {Entry[]} leftovers - Remaining entries to assign
 * @param {Array<{name: string, limit: number, entries: Entry[], submitters: Set<number>}>} showStates - All shows
 * @param {Xoshiro256StarStar} rng - Random number generator
 */
function assignLeftovers(leftovers, showStates, rng, balanceCeils = null) {
    // Build list of available slots with show references
    const availableSlots = [];
    for (const show of showStates) {
        const remainingCapacity = show.limit - show.entries.length;
        for (let i = 0; i < remainingCapacity; i++) {
            availableSlots.push(show);
        }
    }

    const slotCount = availableSlots.length;
    const leftoverCount = leftovers.length;

    if (leftoverCount > slotCount) {
        throw new Error(`Cannot assign ${leftoverCount} leftovers to ${slotCount} available slots`);
    }

    // Bipartite matching for leftovers
    const slotToEntry = Array(slotCount).fill(-1);
    const seen = Array(slotCount);

    /**
     * DFS to find augmenting path for leftover assignment
     * @param {number} entryIndex
     * @returns {boolean}
     */
    function findAugmentingPath(entryIndex) {
        const entry = leftovers[entryIndex];
        const submitter = entry.submitter;

        for (let slotIndex = 0; slotIndex < slotCount; ++slotIndex) {
            if (seen[slotIndex]) continue;

            const show = availableSlots[slotIndex];
            if (show.submitters.has(submitter)) continue;
            // Honour the global balance ceilings here too — without
            // them, phase 1 leftovers (entries that couldn't fit under
            // their genre/language cap) would all land in the same show.
            if (balanceCeils && show.balanceCounts) {
                let blocked = false;
                for (const key of BALANCE_KEYS) {
                    const v = entry[key];
                    if (!v) continue;
                    const cap = balanceCeils[key]?.get(v);
                    if (cap !== undefined
                        && (show.balanceCounts[key].get(v) || 0) >= cap) {
                        blocked = true;
                        break;
                    }
                }
                if (blocked) continue;
            }

            seen[slotIndex] = true;

            if (slotToEntry[slotIndex] === -1 || findAugmentingPath(slotToEntry[slotIndex])) {
                slotToEntry[slotIndex] = entryIndex;
                return true;
            }
        }
        return false;
    }

    // Match each leftover entry
    for (let entryIndex = 0; entryIndex < leftoverCount; ++entryIndex) {
        seen.fill(false);
        if (!findAugmentingPath(entryIndex)) {
            throw new Error(`Cannot place entry from submitter ${leftovers[entryIndex].submitter}: no compatible shows available`);
        }
    }

    // Apply the matching
    for (let slotIndex = 0; slotIndex < slotCount; ++slotIndex) {
        const entryIndex = slotToEntry[slotIndex];
        if (entryIndex !== -1) {
            const show = availableSlots[slotIndex];
            const entry = leftovers[entryIndex];
            show.entries.push(entry);
            show.submitters.add(entry.submitter);
            if (show.codes) show.codes.add(entry.code);
            if (show.balanceCounts) {
                for (const key of BALANCE_KEYS) {
                    const v = entry[key];
                    if (!v) continue;
                    show.balanceCounts[key].set(
                        v,
                        (show.balanceCounts[key].get(v) || 0) + 1,
                    );
                }
            }
        }
    }
}

/**
 * Single-pot (specials) allocation.
 *
 * Bipartite matching gives up on the country-spread constraint as soon
 * as a single pass fails, which lets a country with many entries pile
 * into the same semifinal. For specials we want a stronger guarantee:
 * place the duplicate-country entries first, round-robin across shows,
 * before any singletons fight for the remaining slots.
 *
 * Algorithm:
 *   1. Group entries by country code.
 *   2. Process duplicate-country groups, largest first. For each group,
 *      shuffle the entries, then deal them out to shows one-by-one in a
 *      randomly-rotated round-robin order — skipping any show that's
 *      full or already has this submitter. The result is the maximum
 *      possible spread (for K entries across N shows: ⌈K/N⌉ in some
 *      shows, ⌊K/N⌋ in the rest).
 *   3. Fill the remaining slots with singletons, picking the show with
 *      the most remaining capacity (and no submitter conflict) so the
 *      shows finish at their target limits.
 *
 * @param {Entry[]} entries
 * @param {Array<{name: string, limit: number, entries: Entry[], submitters: Set<number>}>} showStates
 * @param {Xoshiro256StarStar} rng
 */
function drawSinglePotBalanced(entries, showStates, rng) {
    // Track per-show counts of country codes and each balance key.
    // Initialized from any pre-existing entries so re-runs over
    // partially-filled shows still balance correctly.
    for (const s of showStates) {
        s.codeCounts = new Map();
        if (!s.balanceCounts) {
            s.balanceCounts = {};
            for (const key of BALANCE_KEYS) s.balanceCounts[key] = new Map();
        }
        for (const e of s.entries) {
            s.codeCounts.set(e.code, (s.codeCounts.get(e.code) || 0) + 1);
            for (const key of BALANCE_KEYS) {
                const v = e[key];
                if (!v) continue;
                s.balanceCounts[key].set(v, (s.balanceCounts[key].get(v) || 0) + 1);
            }
        }
    }

    const byCountry = new Map();
    for (const entry of entries) {
        if (!byCountry.has(entry.code)) byCountry.set(entry.code, []);
        byCountry.get(entry.code).push(entry);
    }

    const dupGroups = [];
    const singletons = [];
    for (const group of byCountry.values()) {
        if (group.length > 1) dupGroups.push(group);
        else singletons.push(group[0]);
    }
    dupGroups.sort((a, b) => b.length - a.length);
    for (const g of dupGroups) rng.shuffle(g);
    rng.shuffle(singletons);

    function place(entry, target) {
        target.entries.push(entry);
        target.submitters.add(entry.submitter);
        target.codeCounts.set(entry.code, (target.codeCounts.get(entry.code) || 0) + 1);
        for (const key of BALANCE_KEYS) {
            const v = entry[key];
            if (!v) continue;
            target.balanceCounts[key].set(
                v,
                (target.balanceCounts[key].get(v) || 0) + 1,
            );
        }
    }

    /**
     * Lower score = better target. Composite key gives strict priority:
     *   1. fewest entries from this country (the user-asked-for hard
     *      country round-robin).
     *   2. summed balance-key counts (genre + language): secondary
     *      balance across the soft-balance dimensions.
     *   3. most remaining capacity (so the shows finish at their
     *      target sizes).
     *   4. small random jitter to break otherwise-perfect ties so
     *      redraws aren't identical.
     */
    function score(show, entry) {
        const codeCount = show.codeCounts.get(entry.code) || 0;
        let balanceCount = 0;
        for (const key of BALANCE_KEYS) {
            const v = entry[key];
            if (!v) continue;
            balanceCount += show.balanceCounts[key].get(v) || 0;
        }
        const fill = show.entries.length;
        const remaining = show.limit - fill;
        return (
            codeCount * 1_000_000
            + balanceCount * 1_000
            - remaining * 10
            + rng.nextFloat()
        );
    }

    function pickBest(entry) {
        const candidates = showStates.filter(
            s => s.entries.length < s.limit && !s.submitters.has(entry.submitter),
        );
        if (!candidates.length) {
            throw new Error(
                `Cannot place entry from ${entry.code}/${entry.submitter}: every show is full or already has this submitter`,
            );
        }
        candidates.sort((a, b) => score(a, entry) - score(b, entry));
        return candidates[0];
    }

    // Round-robin each duplicate-country group across shows. Country
    // count is the dominant term in `score`, so the show with the
    // fewest of this country always wins — the genre/fill terms only
    // resolve ties between equally-balanced shows.
    for (const group of dupGroups) {
        for (const entry of group) {
            place(entry, pickBest(entry));
        }
    }

    // Fill remaining slots with singletons. Each singleton's country is
    // unique here (count 0 in every show), so genre/capacity decide.
    for (const entry of singletons) {
        place(entry, pickBest(entry));
    }
}

/**
 * Main allocation algorithm - distributes entries from pots into shows
 * @param {Entry[][]} pots - Array of pots containing entries
 * @param {Object<string, {limit: number, entries: Entry[]}>} shows - Show configurations
 * @param {Xoshiro256StarStar} rng - Random number generator
 * @throws {Error} If allocation fails
 */
function drawIntoShows(pots, shows, rng) {
    if (!rng?.next) {
        throw new Error('Valid RNG with next(limit) method required');
    }

    validateAllocation(pots, shows);

    // Prepare show states with tracking
    const showNames = Object.keys(shows);
    const showStates = showNames.map(name => {
        const initialEntries = shows[name].entries;
        const balanceCounts = {};
        for (const key of BALANCE_KEYS) {
            balanceCounts[key] = new Map();
            for (const e of initialEntries) {
                const v = e[key];
                if (!v) continue;
                balanceCounts[key].set(v, (balanceCounts[key].get(v) || 0) + 1);
            }
        }
        return {
            name,
            limit: shows[name].limit,
            entries: initialEntries,
            submitters: new Set(initialEntries.map(e => e.submitter)),
            // For balanced country distribution (relevant when a single
            // country has multiple entries — i.e. specials).
            codes: new Set(initialEntries.map(e => e.code)),
            // For balanced soft-balance distribution across shows.
            balanceCounts,
        };
    });

    // Specials use a single combined pot with potentially many entries
    // per country. Use a dedicated balancer that guarantees maximum
    // country spread instead of bipartite-with-fallback.
    if (pots.length === 1) {
        drawSinglePotBalanced(pots[0], showStates, rng);
        pots[0].length = 0;
        return;
    }

    // Shuffle each pot for randomization
    pots.forEach(rng.shuffle);

    // Compute the per-key ceilings once over the union of all entries
    // so phase 1 (per-pot matching) and phase 2 (leftovers) share a
    // single target — otherwise phase 2 would happily pile leftover
    // entries of a saturated genre or language into one show.
    const allEntries = pots.flat();
    const balanceCeils = {};
    for (const key of BALANCE_KEYS) {
        balanceCeils[key] = ceilingByKey(allEntries, key, showStates.length);
    }

    // Phase 1: Assign complete pots to shows
    while (true) {
        const liveShows = showStates.filter(s => s.entries.length < s.limit);
        if (!liveShows.length) break;

        // Check if we have enough entries in each pot
        if (!pots.every(pot => pot.length >= liveShows.length)) break;

        // Randomize show order for fairness
        rng.shuffle(liveShows);

        // Track whether any pot shrank — if the country/submitter/genre
        // constraints make no further matches possible (e.g. every
        // remaining entry conflicts with every live show because of the
        // country-spread or genre-cap rule), bail out and let the
        // leftovers phase handle them with the relaxed constraint.
        let progress = false;
        for (const pot of pots) {
            const before = pot.length;
            if (!assignPotToShows(pot, liveShows, rng, balanceCeils)) {
                throw new Error('Pot assignment failed: conflicting submitter constraints');
            }
            if (pot.length < before) progress = true;
        }
        if (!progress) break;
    }

    // Phase 2: Handle remaining entries
    const leftovers = rng.shuffle(pots.flat());
    if (leftovers.length > 0) {
        try {
            assignLeftovers(leftovers, showStates, rng, balanceCeils);
        } catch {
            // A balance cap may make a feasible placement impossible
            // when submitter constraints chain in unfortunate ways.
            // assignLeftovers throws *before* modifying state, so it's
            // safe to retry without the caps.
            assignLeftovers(leftovers, showStates, rng, null);
        }
    }

    // Verify final state
    for (const show of showStates) {
        if (show.entries.length !== show.limit) {
            throw new Error(`Show ${show.name} has ${show.entries.length} entries but needs ${show.limit}`);
        }
    }
}

/**
 * Order entries into running-order positions such that any country with
 * multiple entries in the same show is spaced apart, and entries
 * sharing a genre tag aren't bunched up next to each other in the
 * running order. Single-occurrence countries fill the remaining slots
 * in random order, then a swap pass de-clumps adjacent same-genre tiles.
 *
 * @param {Entry[]} entries
 * @param {Xoshiro256StarStar} rng
 * @returns {Entry[]} entries placed at indices 0..N-1
 */
function spreadEntries(entries, rng) {
    const N = entries.length;
    if (N === 0) return [];

    // Group by country code.
    const byCountry = new Map();
    for (const entry of entries) {
        if (!byCountry.has(entry.code)) byCountry.set(entry.code, []);
        byCountry.get(entry.code).push(entry);
    }
    // Randomize which sibling lands in which target slot.
    for (const group of byCountry.values()) rng.shuffle(group);

    const multi = [...byCountry.values()].filter(g => g.length > 1);
    const single = [...byCountry.values()].filter(g => g.length === 1).map(g => g[0]);
    rng.shuffle(single);
    // Place larger groups first — they need the most spread.
    multi.sort((a, b) => b.length - a.length);

    const result = new Array(N).fill(null);

    for (const group of multi) {
        const k = group.length;
        const stride = N / k;
        // Random base offset (within one stride window) so the spread
        // doesn't always start at slot 0.
        const span = Math.max(1, Math.floor(stride));
        const base = rng.next(span);
        for (let i = 0; i < k; i++) {
            let pos = Math.floor(base + i * stride) % N;
            // Skip slots already taken by an earlier (larger) group.
            while (result[pos] !== null) pos = (pos + 1) % N;
            result[pos] = group[i];
        }
    }

    // Fill remaining slots in shuffled order.
    let cursor = 0;
    for (const entry of single) {
        while (result[cursor] !== null) cursor++;
        result[cursor] = entry;
    }

    // Adjacency de-clumping pass: walk the running order and, whenever
    // two neighbours share any of {country, genre, language, submitter},
    // look further ahead for an entry that can swap in without
    // creating a fresh adjacency conflict. Bounded loop to guarantee
    // termination on pathological inputs (e.g. when most entries share
    // a genre).
    const ADJACENCY_KEYS = ['code', 'genre', 'language', 'submitter'];
    function conflictsWith(entry, neighbour) {
        if (!neighbour || !entry) return false;
        for (const key of ADJACENCY_KEYS) {
            const v = entry[key];
            if (v !== undefined && v !== null && v !== '' && neighbour[key] === v) {
                return true;
            }
        }
        return false;
    }

    for (let pass = 0; pass < 6; pass++) {
        let swapped = false;
        for (let i = 0; i < N - 1; i++) {
            const a = result[i];
            const b = result[i + 1];
            if (!a || !b || !conflictsWith(a, b)) continue;
            // b clashes with a — try to swap b for some later entry c.
            for (let j = i + 2; j < N; j++) {
                const c = result[j];
                if (!c) continue;
                if (conflictsWith(a, c)) continue;
                // After swap, b lands at j and c at i+1. Check both
                // sides for fresh conflicts.
                const right_i = result[i + 2] ?? null;
                const left_j = result[j - 1];
                const right_j = result[j + 1] ?? null;
                if (conflictsWith(c, right_i === b ? null : right_i)) continue;
                if (conflictsWith(b, left_j === b ? null : left_j)) continue;
                if (conflictsWith(b, right_j === b ? null : right_j)) continue;
                result[i + 1] = c;
                result[j] = b;
                swapped = true;
                break;
            }
        }
        if (!swapped) break;
    }

    return result;
}

/**
 * Updates show UI with assigned entries
 * @param {string} showName - Name of the show
 * @param {Entry[]} showEntries - Entries assigned to this show
 * @param {Xoshiro256StarStar} rng - Random number generator
 */
function setShowDraw(showName, showEntries, rng) {
    console.log(showEntries);
    const elements = [...document.querySelectorAll(`.show[data-name='${showName}'] .show-country`)];
    // Slots are pre-rendered with data-index = 0..N-1 (the running order
    // position). Sort so result[i] lands at slot i.
    elements.sort((a, b) => parseInt(a.dataset.index) - parseInt(b.dataset.index));

    const ordered = spreadEntries(showEntries.slice(), rng);

    for (let i = 0; i < ordered.length; i++) {
        const el = elements[i];
        const en = ordered[i];

        el.dataset.id = en.id;
        el.dataset.code = en.code;
        el.dataset.genre = en.genre || "";
        el.dataset.language = en.language || "";
        el.dataset.pot = en.pot;
        el.querySelector('.flag').src = en.element.querySelector('.flag').src;
        el.querySelector('.country-name').textContent = en.element.querySelector('.country-name').textContent;
        // Specials carry a song-title element on both pot tiles and show
        // slots; copy the text across when present.
        const titleSrc = en.element.querySelector('.song-title');
        const titleDst = el.querySelector('.song-title');
        if (titleSrc && titleDst) {
            titleDst.textContent = titleSrc.textContent;
        }
        en.element.dataset.show = showName;
    }
}

let shows = null;
const rng = new Xoshiro256StarStar(year);

/**
 * Performs multi-show draw with constraint satisfaction
 */
function drawShowsMulti() {
    if (!multiDraw) {
        throw new Error("Cannot draw multiple shows in single-draw mode");
    }

    const showData = {};
    [...document.querySelectorAll(".show")].forEach(e => {
        showData[e.dataset.name] = {
            limit: parseInt(e.dataset.songs),
            entries: []
        }
    });

    const pots = [...document.querySelectorAll(".pot")].map((e, i) =>
        [...e.querySelectorAll(".pot-item")].map(e => new Entry(e, i))
    );

    drawIntoShows(pots, showData, rng);

    // Sort entries by pot for display
    for (const show of Object.values(showData)) {
        show.entries.sort((a, b) => a.pot - b.pot);
    }

    for (const [showName, data] of Object.entries(showData)) {
        setShowDraw(showName, data.entries, rng);
    }

    shows = showData;
}

/**
 * Performs single-show draw without constraints
 */
function drawShowsSingle() {
    if (multiDraw) {
        throw new Error("Cannot draw single show in multi-draw mode");
    }

    const showData = {};
    [...document.querySelectorAll(".show")].forEach(e => {
        showData[e.dataset.name] = {
            limit: parseInt(e.dataset.songs),
            entries: []
        }
    });

    if (Object.keys(showData).length !== 1) {
        throw new Error("Single-draw mode requires exactly one show");
    }

    const showName = Object.keys(showData)[0];
    const show = showData[showName];

    // Collect all entries from all pots
    const allEntries = [...document.querySelectorAll(".pot")].flatMap((e, i) =>
        [...e.querySelectorAll(".pot-item")].map(e => new Entry(e, i))
    );

    // Validate capacity
    if (allEntries.length > show.limit) {
        throw new Error(`Cannot fit ${allEntries.length} entries into ${show.limit} slots`);
    }

    // Check submitter constraints even in single-show mode
    const submitterCounts = new Map();
    for (const entry of allEntries) {
        submitterCounts.set(entry.submitter, (submitterCounts.get(entry.submitter) ?? 0) + 1);
    }

    show.entries = allEntries;
    setShowDraw(showName, show.entries, rng);
    shows = showData;
}

/**
 * Main entry point for drawing shows
 */
function drawShows() {
    try {
        if (multiDraw) {
            drawShowsMulti();
        } else {
            drawShowsSingle();
        }
    } catch (error) {
        console.error('Draw failed:', error);
        // Reset state on failure
        shows = null;
        throw error;
    }
}

/**
 * @param {HTMLElement} element
 */
function nextSibling(element) {
    const v = element.nextElementSibling;
    if (v == null) {
        return [element.parentElement.firstElementChild, true];
    }
    return [v, false];
}

const minDelay = 10;
const maxDelay = 175;

/**
 * @param {HTMLElement} element
 * @param {HTMLElement[]} elements
 * @param {(HTMLElement, HTMLElement) => boolean} equal
 */
async function animateElementSelect(element, elements, equal) {
    const nElems = elements.length;

    async function mainLoop(current, i, cycles) {
        const progress = i / cycles;
        const eased = easeOutCubic(progress);
        const delay = minDelay + eased * (maxDelay - minDelay);

        if (current) current.classList.remove("active2");
        current = elements[i % nElems];
        current.classList.add("active2");

        await new Promise(r => setTimeout(r, delay));

        return current;
    }

    const index = elements.indexOf(element);
    if (index == -1) {
        throw new Error("Element not part of elements");
    }

    let loops = ((rng.next(5) + 1) % 3);
    if (elements.length < 5) loops += 1;
    if (index < 5) loops += 1;

    const cycles = loops * elements.length;
    const totalCycles = cycles + index;

    let current = null;
    let i = 0;

    for (; i <= cycles; i++) {
        current = await mainLoop(current, i, totalCycles);
    }

    for (; !equal(element, current); i++) {
        current = await mainLoop(current, i, totalCycles);
    }
}

/**
 * @param {HTMLElement} a
 * @param {HTMLElement} b
 * @returns {boolean}
 */
function cmpItems(a, b) {
    // Prefer song id when available — for specials a country can have
    // multiple entries, so data-code isn't unique.
    if (a.dataset.id && b.dataset.id) {
        return a.dataset.id === b.dataset.id;
    }
    return a.dataset.code === b.dataset.code;
}

/**
 * @param {HTMLElement[]} allCountries
 * @param {HTMLElement[]} eligibleCountries
 * @returns {Promise<HTMLElement>}
 */
async function selectCountryFromPot(allCountries, eligibleCountries) {
    const selectedCountry = rng.select(eligibleCountries);
    await animateElementSelect(selectedCountry, allCountries, cmpItems);
    return selectedCountry;
}

/**
 * @param {HTMLElement} selected
 * @param {HTMLElement} currentShow
 * @returns {Promise<HTMLElement>}
 */
async function getShowSlot(selected, currentShow) {
    const allSlots = [...currentShow.querySelectorAll(".show-country.transparent")];
    // Match by song id when present (handles specials with duplicate
    // country codes), falling back to country code for legacy callers.
    const suitableSlot = selected.dataset.id
        ? currentShow.querySelector(`.show-country[data-id="${selected.dataset.id}"]`)
        : currentShow.querySelector(`.show-country[data-code="${selected.dataset.code}"]`);
    await animateElementSelect(suitableSlot, allSlots, cmpItems);
    return suitableSlot;
}

let clicked = false;

async function next() {
    if (clicked) return;
    clicked = true;
    if (shows == null) drawShows();

    const currentPotElement = document.querySelector('.pot.active1');
    const currentPot = currentPotElement.querySelector('.pot-container');
    const currentShowElement = document.querySelector('.show.active1');
    const currentShow = currentShowElement.querySelector('.show-countries');

    try {
        let selected;
        if (multiDraw) {
            const allCountries = [...currentPot.querySelectorAll('.pot-item')];
            const eligibleCountries = [...currentPot.querySelectorAll(`.pot-item[data-show='${currentShow.dataset.name}']`)];
            selected = await selectCountryFromPot(allCountries, eligibleCountries);
        } else {
            selected = document.querySelector(".pot-item:first-child");
        }
        const showSlot = await getShowSlot(selected, currentShow);
        showSlot.classList.remove("transparent");

        await new Promise(r => setTimeout(r, 1000));

        selected.remove();
        showSlot.classList.remove("active2");
    } catch (e) {
        console.log(e);
    }

    const [nextShow, looped] = nextSibling(currentShowElement);
    currentShowElement.classList.remove("active1");
    nextShow.classList.add("active1");
    // Bring the new active show into view if the #shows container has
    // overflowed off-screen — uses the container's `scroll-behavior:
    // smooth` so the scroll animates rather than jumping.
    nextShow.scrollIntoView({ block: "nearest", inline: "center" });

    if (looped) {
        const [nextPot,] = nextSibling(currentPotElement);
        currentPotElement.classList.remove("active1");
        nextPot.classList.add("active1");
    }

    if (currentPotElement.querySelectorAll('.item').length == 0) {
        currentPotElement.remove();
    }

    clicked = false;
}