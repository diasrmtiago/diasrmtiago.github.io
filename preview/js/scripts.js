/*!
* Start Bootstrap - Clean Blog v6.0.7 (https://startbootstrap.com/theme/clean-blog)
* Copyright 2013-2021 Start Bootstrap
* Licensed under MIT (https://github.com/StartBootstrap/startbootstrap-clean-blog/blob/LICENSE)
*/
(function () {
    if (document.querySelector('link[href*="theme.css"]')) {
        return;
    }
    const inPosts = /\/POSTS\//.test(window.location.pathname);
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = (inPosts ? "../" : "") + "css/theme.css";
    document.head.appendChild(link);
})();

window.addEventListener("DOMContentLoaded", () => {
    const mainNav = document.getElementById("mainNav");
    if (!mainNav) {
        return;
    }

    const onScroll = () => {
        mainNav.classList.toggle("is-scrolled", window.scrollY > 24);
    };

    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
});
