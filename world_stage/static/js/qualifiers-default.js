(function () {
    "use strict";

    function createEnvelopePart(name, width, height) {
        const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
        use.setAttributeNS("http://www.w3.org/1999/xlink", "xlink:href", `#${name}`);
        use.setAttribute("width", width);
        use.setAttribute("height", height);

        const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
        svg.appendChild(use);
        svg.setAttribute("width", width);
        svg.setAttribute("height", height);

        const part = document.createElement("div");
        part.classList.add(name, "envelope-part");
        part.appendChild(svg);
        return part;
    }

    function createCard(country, context) {
        const label = context.entryLabel(country);
        const card = document.createElement("div");
        card.classList.add("envelope-card", "hidden", "shrunk");

        const back = document.createElement("div");
        back.classList.add("card-back");
        card.appendChild(back);

        const front = document.createElement("div");
        front.classList.add("card-front", "flipped");
        card.appendChild(front);

        const flag = document.createElement("img");
        flag.src = window.flagStaticUrl(country.cc, 54);
        flag.classList.add("card-flag", "flag-image");
        flag.draggable = false;
        flag.title = country.country;
        front.appendChild(flag);

        const title = document.createElement("h2");
        title.textContent = label;
        title.title = label;
        title.classList.add(context.isSpecial ? "card-title" : "card-country");
        front.appendChild(title);
        return card;
    }

    function createEnvelope(n, country, isSecondChance, context) {
        const envelope = document.createElement("div");
        envelope.classList.add(
            "envelope",
            isSecondChance ? "second-chance" : "direct-to-final"
        );
        envelope.dataset.id = context.entryKey(country);
        envelope.dataset.cc = country.cc;
        envelope.dataset.song = country.id;
        if (country.is_special) envelope.title = "Special qualifier";

        envelope.appendChild(createEnvelopePart("envelope-bg", 180, 120));
        envelope.appendChild(createEnvelopePart("envelope-bottom", 180, 120));
        envelope.appendChild(createEnvelopePart("envelope-top", 180, 60));

        const number = document.createElement("h2");
        number.textContent = n;
        number.classList.add("envelope-number");
        envelope.appendChild(number);
        envelope.appendChild(createCard(country, context));
        return envelope;
    }

    function createCountry(country, countryClass, context) {
        const element = document.createElement("div");
        element.classList.add("country", countryClass);
        element.dataset.id = context.entryKey(country);
        element.dataset.cc = country.cc;

        const flag = document.createElement("img");
        flag.classList.add("reveal-flag", "flag-image");
        flag.draggable = false;
        flag.src = window.flagStaticUrl(country.cc, 24, "square");
        flag.title = country.country;
        element.appendChild(flag);

        const label = context.entryLabel(country);
        const heading = document.createElement("h2");
        heading.classList.add(context.isSpecial ? "reveal-title" : "reveal-country");
        heading.textContent = label;
        heading.title = label;
        element.appendChild(heading);
        return element;
    }

    window.qualifiersTheme = {createEnvelope, createCountry};
})();
