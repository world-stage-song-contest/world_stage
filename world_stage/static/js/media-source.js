window.WorldStageMediaSource = {
    async resolve(url) {
        if (!new URL(url, window.location.href).pathname.toLowerCase().endsWith('.json')) {
            return { url };
        }
        const response = await fetch(url);
        if (!response.ok) throw new Error(`Media request failed (${response.status}).`);
        const manifest = await response.json();
        const source = manifest.sources?.find(source => (
            typeof source.url === 'string' && source.url
        ));
        if (!source) throw new Error('The media manifest has no playable source.');
        const sourceUrl = new URL(source.url, response.url || url);
        if (!['http:', 'https:'].includes(sourceUrl.protocol)) {
            throw new Error('The media source URL is invalid.');
        }
        return {
            url: sourceUrl.href,
            contentType: source.contentType,
            thumbnail: manifest.thumbnail
        };
    }
};
