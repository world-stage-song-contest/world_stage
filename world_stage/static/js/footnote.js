/**
 * Initializes the footnotes by setting the data-footnote-content attribute
 * on each footnote link to the corresponding footnote text.
 * This allows the footnote text to be displayed when the link is hovered over.
 * The footnote is an anchor element with a class of "footnote-link".
 * The href attribute of the link is set to the ID of the footnote.
 * The footnote text is contained within a span element without a class.
 */
function onLoad() {
    const footnotes = document.querySelectorAll('.footnote-link');
    footnotes.forEach(footnote => {
        const fnid = footnote.href.split('#')[1];
        console.log(fnid);
        const fnText = document.getElementById(fnid).textContent;
        footnote.dataset.footnoteContent = fnText;
    });
}