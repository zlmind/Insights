(function () {
    'use strict';
    var $ = function (s, r) { return (r || document).querySelector(s); };
    var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };

    /* ---------- Toast ---------- */
    var toastEl = $('#toast'), toastTimer = null;
    function toast(msg) {
        toastEl.textContent = msg;
        toastEl.classList.add('show');
        clearTimeout(toastTimer);
        toastTimer = setTimeout(function () { toastEl.classList.remove('show'); }, 2600);
    }

    /* ---------- 屏幕切换 ---------- */
    var TITLES = {
        compose:   { t: '发布',     meta: '已选 <b id="tbSelN">3</b> 个平台',
                     acts: '<button class="btn btn-ghost" data-modal="promptModal">提示词库</button>' },
        briefs:    { t: '智能写作', meta: '2 个任务进行中',
                     acts: '' },
        articles:  { t: '文章',     meta: '12 篇 · 4 篇草稿',
                     acts: '<button class="btn btn-primary" data-act="新建文章（原型未实现）">新建文章</button>' },
        thumbnails:{ t: '封面',     meta: '粘贴视频链接，生成候选封面',
                     acts: '' },
        feeds:     { t: '订阅源',   meta: '6 个订阅源 · 238 个条目',
                     acts: '' },
        sources:   { t: '保存的链接', meta: '8 条已保存',
                     acts: '' },
        status:    { t: '处理中',   meta: '2 个任务进行中',
                     acts: '' },
        tickets:   { t: '工单',     meta: '5 个开放',
                     acts: '<button class="btn btn-primary" data-act="新建工单（原型未实现）">新建工单</button>' },
        calendar:  { t: '日历',     meta: '10 月已排期 7 篇',
                     acts: '<button class="btn btn-outline" data-act="回到今天（原型未实现）">今天</button>' +
                           '<button class="btn btn-primary" data-goto="compose">新建发布</button>' },
        jobs:      { t: '发布记录', meta: '今天 7 个任务 · 1 个失败',
                     acts: '' },
        accounts:  { t: '平台账号', meta: '4 个平台已登录 · 头条号登录失效',
                     acts: '' },
        usage:     { t: '用量与成本', meta: '2026 年 10 月',
                     acts: '' },
        activity:  { t: '活动日志', meta: '谁在什么时候改了什么',
                     acts: '' },
        settings:  { t: '设置',     meta: '实例配置 · API 密钥 · 模型',
                     acts: '<button class="btn btn-primary" data-act="保存设置（原型未实现）">保存更改</button>' }
    };
    var app = $('#app');
    function switchScreen(name) {
        $$('[data-screen]').forEach(function (b) {
            var on = b.dataset.screen === name;
            b.classList.toggle('is-active', on);
            if (on) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current');
        });
        $$('[data-screen-panel]').forEach(function (p) {
            p.hidden = p.dataset.screenPanel !== name;
        });
        var conf = TITLES[name];
        if (conf) {
            $('#tbTitle').textContent = conf.t;
            $('#tbMeta').innerHTML = conf.meta;
            $('#tbActions').innerHTML = conf.acts;
        }
        window.scrollTo(0, 0);
        closeDrawer();
    }

    /* ---------- 全局事件委托 ---------- */
    document.addEventListener('click', function (e) {
        var el;
        if ((el = e.target.closest('.nav-item, .side-foot [data-screen]')) ) {
            switchScreen(el.dataset.screen);
            return;
        }
        if ((el = e.target.closest('[data-goto]'))) { switchScreen(el.dataset.goto); return; }
        if ((el = e.target.closest('[data-pairctl]'))) { openModal('pairModal'); return; }
        if ((el = e.target.closest('[data-modal]'))) { openModal(el.dataset.modal); return; }
        if ((el = e.target.closest('[data-close]'))) {
            var back = el.closest('.modal-back');
            if (back) back.hidden = true;
            return;
        }
        if ((el = e.target.closest('[data-act]'))) { toast(el.dataset.act); return; }
        if ((el = e.target.closest('.chip'))) {
            $$('.chip', el.parentElement).forEach(function (c) { c.classList.remove('on'); });
            el.classList.add('on');
            return;
        }
    });

    /* ---------- 弹层 ---------- */
    function openModal(id) {
        var m = document.getElementById(id);
        if (!m) return;
        m.hidden = false;
        var first = m.querySelector('input, button');
        if (first) first.focus();
    }
    $$('.modal-back').forEach(function (back) {
        back.addEventListener('mousedown', function (e) { if (e.target === back) back.hidden = true; });
    });
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') {
            $$('.modal-back').forEach(function (m) { m.hidden = true; });
            closeDrawer();
        }
    });

    /* ---------- 配对流程（发布助手未连接态） ---------- */
    var PAIR_CODE = 'K2XQ-7WDM', pairCopied = false;
    function renderPairUI(on) {
        var c = $('#connBtn');
        if (on) {
            c.innerHTML = '<span class="dot dot-ok" aria-hidden="true"></span><span class="nav-txt">发布助手 · 在线</span>';
            c.title = '发布助手已连接，可推送到需要登录态的平台';
            $('#pairNavBadge').textContent = '7'; $('#pairNavBadge').classList.remove('is-warn');
        } else {
            c.innerHTML = '<span class="dot dot-warn" aria-hidden="true"></span><span class="nav-txt">发布助手 · 未连接</span>';
            c.title = '发布助手未连接，点击查看配对';
            $('#pairNavBadge').textContent = '去配对'; $('#pairNavBadge').classList.add('is-warn');
        }
    }
    $('#pairMintBtn').addEventListener('click', function () {
        $('#pairCodeValue').textContent = PAIR_CODE;
        $('#pairCodeBox').hidden = false;
        toast('配对码已生成，5 分钟内有效');
    });
    $('#pairCopyBtn').addEventListener('click', function () {
        if (pairCopied) { toast('配对码已复制过，可直接到扩展填入'); return; }
        pairCopied = true;
        toast('配对码已复制，去扩展选项页粘贴');
    });
    $('#pairCodeValue').addEventListener('click', simulatePaired);
    function simulatePaired() {
        $('#pairModal').hidden = true;
        renderPairUI(true);
        toast('已配对：发布助手在线');
    }

    /* ---------- 侧栏折叠 / 抽屉 ---------- */
    var collapseBtn = $('#collapseBtn');
    collapseBtn.addEventListener('click', function () {
        var on = app.classList.toggle('is-collapsed');
        collapseBtn.setAttribute('aria-pressed', String(on));
    });
    var scrim = $('#scrim');
    function closeDrawer() { app.classList.remove('side-open'); scrim.hidden = true; }
    $('#menuBtn').addEventListener('click', function () {
        app.classList.add('side-open'); scrim.hidden = false;
    });
    scrim.addEventListener('click', closeDrawer);

    /* ---------- 跳转按钮 ---------- */
    var isMac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
    $('#kbKey').textContent = isMac ? '⌘ K' : 'Ctrl K';
    $('#kbBtn').addEventListener('click', function () { toast('正式版沿用现有 Ctrl+K 跳转面板'); });

    /* ---------- Compose：创建方式切换 ---------- */
    var modePanels = {
        manual: null, freeform: $('#modeFreeform'), text: $('#modeText'),
        image: $('#modeImage'), url: $('#modeLink'), file: $('#modeFile'),
        saved: $('#modeSaved'), import: $('#modeImport')
    };
    // 每种模式的必填输入（用于点「生成帖子」时的首框校验）
    var modeInputs = {
        freeform: '#genFreeform', text: '#genText', url: '#genUrl', saved: '#genSaved'
    };
    $$('[data-mode]').forEach(function (tab) {
        tab.addEventListener('click', function () { activateMode(tab.dataset.mode); });
    });
    function activateMode(mode) {
        $$('[data-mode]').forEach(function (t) {
            t.setAttribute('aria-selected', String(t.dataset.mode === mode));
        });
        Object.keys(modePanels).forEach(function (k) {
            if (modePanels[k]) modePanels[k].hidden = k !== mode;
        });
        $('#editorForm').hidden = mode !== 'manual';
        $('#genMeta').hidden = mode === 'manual';
        $('#postCards').hidden = mode === 'manual';
        renderQueue();
    }
    var CHNAME = { '知': '知乎', '头': '头条号', '信': '公众号', '红': '小红书', '抖': '抖音' };
    var CHCOLOR = { '知': '#0084FF', '头': '#F04142', '信': '#07C160', '红': '#FF2442', '抖': '#161823' };
    var TONENAME = { professional: '专业', casual: '轻松', witty: '俏皮', educational: '科普', promotional: '种草' };
    function currentTone() {
        var on = document.querySelector('#toneChips .pchip.on');
        return on ? on.dataset.tone : 'professional';
    }
    var SAMPLES = [
        '一句话总结：发布工作台改版后，写完到发出去只需三步——勾平台、写内容、点发布。',
        '左侧常驻导航把创作、来源、发布收进一层，页面不再藏进下拉菜单。',
        '为什么把平台账号提为一级入口？登录态是分发工具的第一焦虑点。',
        '知乎、头条号直接发布，公众号、小红书、抖音存草稿箱——系统按平台语义自动分流。',
        '生成的一批帖子逐条编辑、配图、入队，也可以多选批量处理。',
        '日历视图让排期一目了然，发布记录里每个平台的进度都能追。'
    ];
    var genCards = [];

    function esc(s) {
        return String(s).replace(/[&<>"]/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
        });
    }
    function buildCards(n, src) {
        var base = selAccts().map(function (lab) {
            return { ch: lab.dataset.ch, color: lab.dataset.color };
        });
        if (!base.length) base = [{ ch: '知', color: CHCOLOR['知'] }];
        genCards = [];
        for (var i = 0; i < n; i++) {
            genCards.push({
                text: SAMPLES[i % SAMPLES.length],
                chips: base.slice(),
                queued: false, editing: false, used: false,
                src: src, model: $('#genModel').value, tone: currentTone()
            });
        }
        renderCards(); renderQueue();
    }
    function demoGen(btn, fn) {
        if (btn.dataset.busy) return;
        btn.dataset.busy = '1';
        var old = btn.textContent;
        btn.disabled = true; btn.textContent = '生成中…';
        setTimeout(function () {
            delete btn.dataset.busy;
            btn.disabled = false; btn.textContent = old;
            fn();
        }, 700);
    }
    function generatePosts(src, btn) {
        demoGen(btn, function () {
            var n = parseInt($('#genCount').value, 10) || 4;
            buildCards(n, src);
            toast('已生成 ' + n + ' 条帖子，可逐条编辑、配图、入队');
        });
    }
    // 统一「生成帖子」按钮：data-gen 标注来源模式
    document.addEventListener('click', function (e) {
        var btn = e.target.closest('[data-gen]');
        if (!btn) return;
        var label = btn.dataset.gen;
        if (label === '导入文件') return;
        var cur = document.querySelector('[data-mode][aria-selected="true"]');
        var mode = cur ? cur.dataset.mode : 'url';
        if (mode === 'saved' && !$('#genSaved').value) { toast('先选择一个已保存的来源'); return; }
        if (modeInputs[mode]) {
            var inp = $(modeInputs[mode]);
            if (inp && inp.tagName !== 'SELECT' && !inp.value.trim()) { toast('先把内容填上'); return; }
        }
        generatePosts(label, btn);
    });

    /* ---------- AI 提供商切换 ---------- */
    $('#providerSeg').addEventListener('click', function (e) {
        var b = e.target.closest('[data-provider]');
        if (!b) return;
        $$('#providerSeg .seg-btn').forEach(function (x) {
            var on = x === b;
            x.classList.toggle('on', on);
            x.setAttribute('aria-pressed', String(on));
        });
        var providers = { openai: 'gpt-5.2', anthropic: 'claude-haiku-4.5', local: '本地 Ollama' };
        $('#genModel').value = providers[b.dataset.provider];
    });

    /* ---------- 帖子卡片 ---------- */
    function renderCards() {
        var wrap = $('#postCards');
        $$('.post-card', wrap).forEach(function (el) { el.remove(); });
        $('#pcEmpty').hidden = genCards.length > 0;
        genCards.forEach(function (c, i) {
            var el = document.createElement('div');
            el.className = 'card post-card' + (c.queued ? ' is-queued' : '');
            el.dataset.i = i;
            var chips = c.chips.map(function (p) {
                return '<button class="pchip" data-ch="' + p.ch + '"' + (c.queued ? ' disabled' : '') +
                       '><span class="pmark sm" style="--pc:' + p.color + '">' + p.ch + '</span>' + CHNAME[p.ch] + '</button>';
            }).join('');
            var acts = c.editing
                ? '<button class="linklike" data-pact="save">保存</button>' +
                  '<button class="linklike" data-pact="cancel">取消</button>'
                : '<button class="linklike" data-pact="edit">编辑</button>' +
                  '<button class="linklike" data-pact="img">配图</button>' +
                  '<button class="linklike" data-pact="queue">' + (c.queued ? '出队' : '入队') + '</button>' +
                  '<button class="linklike is-danger" data-pact="del">删除</button>';
            var body = c.editing ? '<textarea class="input">' + esc(c.text) + '</textarea>' : esc(c.text);
            el.innerHTML =
                '<div class="pc-head"><div class="chip-row">' + chips + '</div><div class="pc-acts">' + acts + '</div></div>' +
                '<div class="pc-body">' + body + '</div>' +
                '<div class="pc-meta">' + c.text.length + ' 字 · 从' + c.src + '生成 · ' + esc(c.model) +
                ' · ' + (TONENAME[c.tone] || c.tone) +
                (c.queued ? ' · <b style="color:var(--brand-600)">已入队</b>' : '') + '</div>';
            wrap.appendChild(el);
        });
    }
    $('#postCards').addEventListener('click', function (e) {
        var cardEl = e.target.closest('.post-card');
        if (!cardEl) return;
        var i = +cardEl.dataset.i, c = genCards[i];
        var act = e.target.closest('[data-pact]');
        if (act) {
            var p = act.dataset.pact;
            if (p === 'queue') { c.queued = !c.queued; renderCards(); renderQueue(); }
            else if (p === 'del') { genCards.splice(i, 1); renderCards(); renderQueue(); }
            else if (p === 'img') { toast('配图：上传 / URL / 图库（原型未实现）'); }
            else if (p === 'edit') { c.editing = true; renderCards(); }
            else if (p === 'save') { c.text = cardEl.querySelector('textarea').value; c.editing = false; renderCards(); renderQueue(); }
            else if (p === 'cancel') { c.editing = false; renderCards(); }
            return;
        }
        var chip = e.target.closest('.pchip');
        if (!chip) return;
        if (c.queued) { toast('已入队的帖子先出队再改平台'); return; }
        var ch = chip.dataset.ch;
        var has = c.chips.some(function (x) { return x.ch === ch; });
        if (has && c.chips.length === 1) { toast('至少保留一个平台'); return; }
        c.chips = has
            ? c.chips.filter(function (x) { return x.ch !== ch; })
            : c.chips.concat([{ ch: ch, color: CHCOLOR[ch] }]);
        renderCards(); renderQueue();
    });

    /* ---------- 语气风格（单选） ---------- */
    $('#toneChips').addEventListener('click', function (e) {
        var chip = e.target.closest('.pchip[data-tone]');
        if (!chip) return;
        $$('#toneChips .pchip').forEach(function (c) {
            var on = c === chip;
            c.classList.toggle('on', on);
            c.setAttribute('aria-pressed', String(on));
        });
    });

    /* ---------- Compose：账号选择 → 计数 / 队列 / 发布按钮 ---------- */
    var acctInputs = $$('.acct input[type="checkbox"]');
    function selAccts() {
        return acctInputs.filter(function (i) { return i.checked; });
    }
    function renderQueue() {
        var sel = selAccts(), n = sel.length;
        var manual = !$('#editorForm').hidden;
        $('#selPill').textContent = '已选 ' + n;
        var selN = $('#tbSelN');
        if (selN) selN.textContent = n;
        var ul = $('#queueList');
        ul.innerHTML = '';
        var count = 0, items = [];
        if (manual) {
            count = n;
            var direct = 0;
            sel.forEach(function (lab) { if (lab.dataset.mode === 'direct') direct++; });
            $('#coverHint').hidden = !(sel.some(function (lab) { return lab.dataset.ch === '信'; }));
            $('#publishBtn').disabled = n === 0;
            $('#publishLabel').textContent = n === 0 ? '请选择平台'
                : (direct === 0 ? '保存到 ' + n + ' 个平台草稿箱' : '发布到 ' + n + ' 个平台');
            $('#queuePill').textContent = n + ' 个平台';
            sel.forEach(function (lab) {
                items.push('<span class="pmark" style="--pc:' + lab.dataset.color + '">' + lab.dataset.ch + '</span>' +
                    '<span class="acct-name">' + esc(lab.dataset.name) + '</span>' +
                    '<span class="q-status"><span class="dot dot-queue" aria-hidden="true"></span>' +
                    (lab.dataset.mode === 'draft' ? '草稿箱 · 排队中' : '直接发布 · 排队中') + '</span>');
            });
        } else {
            $('#coverHint').hidden = true;
            var qc = genCards.filter(function (c) { return c.queued; });
            count = qc.length;
            $('#publishBtn').disabled = qc.length === 0;
            $('#publishLabel').textContent = qc.length ? '推送 ' + qc.length + ' 条帖子' : '在卡片点「入队」';
            $('#queuePill').textContent = qc.length + ' 条帖子';
            qc.forEach(function (c) {
                var marks = c.chips.map(function (p) {
                    return '<span class="pmark sm" style="--pc:' + p.color + '">' + p.ch + '</span>';
                }).join(' ');
                items.push('<span style="display:flex;gap:.3rem;flex:none">' + marks + '</span>' +
                    '<span class="acct-name">' + esc(c.text.slice(0, 16)) + '…</span>' +
                    '<span class="q-status"><span class="dot dot-queue" aria-hidden="true"></span>排队中</span>');
            });
        }
        items.forEach(function (html) {
            var li = document.createElement('li');
            li.innerHTML = html;
            ul.appendChild(li);
        });
        if (!count) {
            var li = document.createElement('li');
            li.style.cssText = 'color:var(--muted);justify-content:center';
            li.textContent = manual ? '尚未选择任何平台' : '还没有已入队的帖子';
            ul.appendChild(li);
        }
    }
    acctInputs.forEach(function (i) { i.addEventListener('change', renderQueue); });

    /* ---------- 编辑器：字数 / 预览联动 ---------- */
    var body = $('#editorBody'), title = $('#editorTitle');
    function renderEditor() {
        $('#charCount').textContent = body.value.length;
        $('#prevTitle').textContent = title.value || '（无标题）';
        $('#prevBody').textContent = body.value.slice(0, 80) + (body.value.length > 80 ? '……' : '');
    }
    body.addEventListener('input', function () { renderEditor(); renderQueue(); });
    title.addEventListener('input', renderEditor);
    $('#publishBtn').addEventListener('click', function () {
        var btn = $('#publishBtn');
        if (btn.dataset.busy) return;
        btn.dataset.busy = '1';
        btn.disabled = true;
        $('#publishLabel').textContent = '入队中…';
        setTimeout(function () {
            delete btn.dataset.busy;
            renderQueue();
            toast(!$('#editorForm').hidden
                ? '已创建发布任务：知乎 / 头条号直接发布，公众号 / 小红书保存到草稿箱（原型演示）'
                : '已推送入队的帖子（原型演示）');
        }, 900);
    });

    /* ---------- 预览 Tabs ---------- */
    var PLAT = {
        '知乎':   { ch: '知', color: '#0084FF', acc: '张三的技术笔记' },
        '头条号': { ch: '头', color: '#F04142', acc: '张三说' },
        '公众号': { ch: '信', color: '#07C160', acc: '张三的小站' }
    };
    var tabs = $$('[role="tab"][data-plat]');
    tabs.forEach(function (tab, idx) {
        tab.addEventListener('click', function () { activateTab(idx); });
        tab.addEventListener('keydown', function (e) {
            var d = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
            if (!d) return;
            e.preventDefault();
            var next = (idx + d + tabs.length) % tabs.length;
            activateTab(next); tabs[next].focus();
        });
    });
    function activateTab(idx) {
        tabs.forEach(function (t, i) {
            t.setAttribute('aria-selected', String(i === idx));
            t.setAttribute('tabindex', i === idx ? '0' : '-1');
        });
        var tab = tabs[idx], p = PLAT[tab.dataset.plat];
        $('#prevMark').style.setProperty('--pc', p.color);
        $('#prevMark').textContent = p.ch;
        $('#prevAcc').textContent = p.acc;
        $('#prevPlat').textContent = tab.dataset.plat;
        $('#prevKind').textContent = tab.dataset.kind;
        $('#prevPlatName').textContent = tab.dataset.plat;
        $('#prevPanel').setAttribute('aria-labelledby', tab.id);
    }

    /* ---------- 日历 ---------- */
    var EVENTS = {
        '2026-10-8':  [{ t: '10:00 · AI 编程工作流实践', ch: '知', c: '#0084FF' }],
        '2026-10-12': [{ t: '18:30 · 产品周报 #12',      ch: '信', c: '#07C160' }],
        '2026-10-14': [{ t: '09:30 · 新版发布工作台上线', ch: '头', c: '#F04142' },
                        { t: '20:00 · AI 工具选型指南',   ch: '知', c: '#0084FF' }],
        '2026-10-16': [{ t: '11:00 · 选题库整理方法',     ch: '信', c: '#07C160' }],
        '2026-10-21': [{ t: '15:00 · 剪辑快捷键大全',     ch: '头', c: '#F04142' }],
        '2026-10-27': [{ t: '19:00 · 季度复盘与规划',     ch: '知', c: '#0084FF' }]
    };
    var now = new Date();
    var curY = now.getFullYear(), curM = now.getMonth();
    function renderCal() {
        $('#calMonth').textContent = curY + ' 年 ' + (curM + 1) + ' 月';
        var monthEvents = 0;
        Object.keys(EVENTS).forEach(function (k) {
            var d = k.split('-');
            if (+d[0] === curY && +d[1] === curM + 1) monthEvents += EVENTS[k].length;
        });
        $('#calCount').textContent = monthEvents ? '本月 ' + monthEvents + ' 篇已排期' : '本月暂无排期';
        var grid = $('#calGrid');
        grid.innerHTML = '';
        var first = new Date(curY, curM, 1);
        var lead = (first.getDay() + 6) % 7;           /* 周一为一周起点 */
        var start = new Date(curY, curM, 1 - lead);
        var today = new Date();
        for (var i = 0; i < 42; i++) {
            var d = new Date(start.getFullYear(), start.getMonth(), start.getDate() + i);
            var cell = document.createElement('div');
            cell.className = 'cal-cell';
            var isToday = d.getFullYear() === today.getFullYear() &&
                          d.getMonth() === today.getMonth() && d.getDate() === today.getDate();
            if (d.getMonth() !== curM) cell.classList.add('is-out');
            if (isToday) cell.classList.add('is-today');
            var dEl = document.createElement('span');
            dEl.className = 'd'; dEl.textContent = d.getDate();
            cell.appendChild(dEl);
            var key = d.getFullYear() + '-' + (d.getMonth() + 1) + '-' + d.getDate();
            (EVENTS[key] || []).slice(0, 3).forEach(function (ev) {
                var b = document.createElement('button');
                b.className = 'ev';
                b.style.setProperty('--pc', ev.c);
                b.title = ev.t;
                b.innerHTML = '<span class="pmark" style="--pc:' + ev.c + '">' + ev.ch + '</span>' +
                              '<span class="ev-t">' + ev.t + '</span>';
                b.addEventListener('click', function () { toast('「' + ev.t.split('· ')[1] + '」 发布详情（原型未实现）'); });
                cell.appendChild(b);
            });
            var extra = (EVENTS[key] || []).length - 3;
            if (extra > 0) {
                var more = document.createElement('span');
                more.className = 'cal-more'; more.textContent = '+' + extra + ' 更多';
                cell.appendChild(more);
            }
            grid.appendChild(cell);
        }
    }
    $('#calPrev').addEventListener('click', function () { curM--; if (curM < 0) { curM = 11; curY--; } renderCal(); });
    $('#calNext').addEventListener('click', function () { curM++; if (curM > 11) { curM = 0; curY++; } renderCal(); });

    /* ---------- 原型说明 ---------- */
    $('#notesClose').addEventListener('click', function () { $('#notesPanel').hidden = true; });

    /* ---------- init ---------- */
    renderQueue(); renderEditor(); renderCal();
})();
