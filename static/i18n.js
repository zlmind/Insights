/*
 * Client-side interface translation (English / 简体中文).
 *
 * Templates hold the UI strings in English. When the request language is
 * `zh`, this walks the DOM and swaps text for the Chinese equivalent using
 * static/i18n/zh.json: an exact-match dictionary plus a few regex rules for
 * strings with numbers baked in ("Delete Selected (3)").
 *
 * A MutationObserver catches content the pages render from JavaScript after
 * load (post cards, search results, alerts), and window.alert/confirm/prompt
 * are wrapped so dialog text goes through the same dictionary. Anything not
 * in the dictionary — including user content — is left as it is, so a new
 * template string is simply untranslated until the dictionary grows.
 *
 * The server picks the language (cookie, then Accept-Language) and exposes
 * it as <html data-lang="...">; `?lang=` is not honoured here on purpose.
 */
(function () {
    var LANG = document.documentElement.getAttribute('data-lang') || 'en';
    if (LANG === 'en') return;

    // Hold the page invisible until the first translation pass has run, so a
    // fresh navigation never flashes its untranslated English. The matching
    // CSS lives in base.html / login.html / signup.html (scoped to
    // html[data-lang="zh"]); if it's missing this class is inert.
    document.documentElement.classList.add('i18n-loading');
    function reveal() { document.documentElement.classList.remove('i18n-loading'); }
    // Backstop: never leave the page hidden. If the dictionary fetch hangs,
    // show whatever is there (English) so the user isn't stuck on a blank page.
    setTimeout(reveal, 8000);

    // Resolve the dictionary URL from this script's own address, so it works
    // wherever the app is mounted (currentScript is valid during sync load).
    var DICT_URL = '/static/i18n/zh.json';
    try {
        var src = document.currentScript && document.currentScript.src;
        if (src) DICT_URL = src.replace(/\/i18n\.js.*$/, '/i18n/zh.json');
    } catch (e) { /* keep the default */ }

    var texts = null;   // Map: english -> 中文
    var rules = [];     // [{re, to}] for strings with numbers/variables

    function t(s) {
        if (typeof s !== 'string' || !s) return s;
        if (texts) {
            var hit = texts.get(s);
            if (hit !== undefined) return hit;
        }
        for (var i = 0; i < rules.length; i++) {
            if (rules[i].re.test(s)) return s.replace(rules[i].re, rules[i].to);
        }
        return s;
    }
    window.t = t;

    function applyRules(entry) {
        texts = new Map();
        var table = entry.texts || entry;
        for (var k in table) {
            if (Object.prototype.hasOwnProperty.call(table, k)) texts.set(k, table[k]);
        }
        rules = (entry.rules || []).map(function (r) {
            return { re: new RegExp(r.re, r.flags || ''), to: r.to };
        });
    }

    /* ---- DOM walking ---------------------------------------------------- */

    // Elements whose text is code or user input, and anything a page marks
    // as its own content with data-i18n-ignore.
    var SKIP_TAGS = { SCRIPT: 1, STYLE: 1, CODE: 1, PRE: 1, TEXTAREA: 1, NOSCRIPT: 1 };

    function inSkipTree(node) {
        for (var el = node; el && el.nodeType === 1; el = el.parentElement) {
            if (SKIP_TAGS[el.tagName] || el.hasAttribute('data-i18n-ignore')) return true;
        }
        return false;
    }

    // Attribute translation (placeholder / title / aria-label) still applies
    // inside TEXTAREA, CODE and PRE — their text content is skipped, but a
    // placeholder is UI chrome. Only script/style/noscript blocks and
    // data-i18n-ignore subtrees are fully off-limits.
    function inSkipAttrTree(node) {
        for (var el = node; el && el.nodeType === 1; el = el.parentElement) {
            if (el.tagName === 'SCRIPT' || el.tagName === 'STYLE' || el.tagName === 'NOSCRIPT' ||
                el.hasAttribute('data-i18n-ignore')) return true;
        }
        return false;
    }

    function translateTextNode(node) {
        var raw = node.nodeValue;
        if (!raw) return;
        var collapsed = raw.replace(/\s+/g, ' ').trim();
        if (!collapsed || /^[0-9\s.,:%()+\-/|·…—]+$/.test(collapsed)) return;
        var zh = t(collapsed);
        if (zh !== collapsed) node.nodeValue = raw.replace(collapsed, zh);
    }

    var TRANSLATED_ATTRS = ['placeholder', 'title', 'aria-label', 'data-bs-original-title'];

    function translateElement(el) {
        for (var i = 0; i < TRANSLATED_ATTRS.length; i++) {
            var name = TRANSLATED_ATTRS[i];
            if (el.hasAttribute && el.hasAttribute(name)) {
                var v = el.getAttribute(name);
                var zh = t(v.replace(/\s+/g, ' ').trim());
                if (zh !== v) el.setAttribute(name, zh);
            }
        }
        // Submit/reset buttons keep their label in value.
        if (el.tagName === 'INPUT' && (el.type === 'submit' || el.type === 'button' || el.type === 'reset')) {
            var zh2 = t(el.value.trim());
            if (zh2 && zh2 !== el.value) el.value = zh2;
        }
        if (el.tagName === 'TITLE') translateTextNode(el.firstChild);
    }

    function translateTree(root) {
        if (!root) return;
        if (root.nodeType === 3) {           // bare text node
            if (!inSkipTree(root.parentElement)) translateTextNode(root);
            return;
        }
        if (root.nodeType !== 1) return;
        var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
        // The walker skips nothing by itself; check each node's ancestors.
        var n;
        while ((n = walker.nextNode())) {
            if (!inSkipTree(n.parentElement)) translateTextNode(n);
        }
        if (!inSkipAttrTree(root)) translateElement(root);
        // Attributes of every descendant element.
        var els = root.querySelectorAll('*');
        for (var i = 0; i < els.length; i++) {
            if (!inSkipAttrTree(els[i])) translateElement(els[i]);
        }
    }

    /* ---- Dialogs and dates ---------------------------------------------- */

    ['alert', 'confirm', 'prompt'].forEach(function (fn) {
        var orig = window[fn];
        if (typeof orig !== 'function') return;
        window[fn] = function (msg) {
            var args = [].slice.call(arguments);
            if (typeof args[0] === 'string') {
                var zh = t(args[0].replace(/\s+/g, ' ').trim());
                if (zh !== args[0]) args[0] = zh;
            }
            return orig.apply(window, args);
        };
    });

    // Dates: unqualified toLocale* calls and explicit English locales switch
    // to the Chinese calendar wording (周五, 10月9日 …).
    function patchDate(name) {
        var orig = Date.prototype[name];
        Date.prototype[name] = function (loc, opts) {
            if (loc == null) loc = 'zh-CN';
            else if (typeof loc === 'string' && /^(en|en-US|en-GB)$/i.test(loc)) loc = 'zh-CN';
            return orig.call(this, loc, opts);
        };
    }
    ['toLocaleDateString', 'toLocaleTimeString', 'toLocaleString'].forEach(patchDate);

    /* ---- Boot ----------------------------------------------------------- */

    function start() {
        translateTree(document.body);
        if (document.title) translateElement(document.head.querySelector('title') || document.body);
        reveal();

        // Pages render most of their UI from JavaScript after load; watch for
        // the additions and translate each batch as it lands.
        var pending = [], scheduled = false;
        function flush() {
            scheduled = false;
            var batch = pending;
            pending = [];
            batch.forEach(translateTree);
        }
        var observer = new MutationObserver(function (muts) {
            for (var i = 0; i < muts.length; i++) {
                var m = muts[i];
                for (var j = 0; j < m.addedNodes.length; j++) {
                    var node = m.addedNodes[j];
                    // Translated text lands as characterData changes on nodes
                    // already in the tree, so only brand-new nodes come here.
                    if (node.nodeType === 1 || node.nodeType === 3) pending.push(node);
                }
            }
            if (!pending.length || scheduled) return;
            scheduled = true;
            Promise.resolve().then(flush);
        });
        observer.observe(document.body, { childList: true, subtree: true });
    }

    fetch(DICT_URL, { cache: 'force-cache' })
        .then(function (r) { return r.json(); })
        .then(function (entry) {
            applyRules(entry);
            if (document.readyState === 'loading') {
                document.addEventListener('DOMContentLoaded', start);
            } else {
                start();
            }
        })
        .catch(function () {
            // No dictionary, no translation — the page stays English. Reveal
            // it so a failed fetch never leaves the page hidden.
            reveal();
        });
})();
