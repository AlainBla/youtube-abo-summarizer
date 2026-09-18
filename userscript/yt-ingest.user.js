// ==UserScript==
// @name         YouTube → Archiv-Ingest
// @namespace    youtube-abo-summarizer
// @version      1.1.0
// @description  Adds a button on YouTube that queues the current video for transcription and summarisation, by driving the Ingest field of your own archive page.
// @author       youtube-abo-summarizer
// @match        https://www.youtube.com/*
// @match        https://m.youtube.com/*
// @match        https://sync.example.com/yt.html*
// @grant        GM_openInTab
// @grant        GM_setValue
// @grant        GM_getValue
// @grant        GM_addValueChangeListener
// @run-at       document-idle
// @noframes
// ==/UserScript==

// On m.youtube.com none of the desktop containers exist, so the button is the
// floating one in the corner there.
//
// ── Configure two things before use ──────────────────────────────────────────
//   1. ARCHIVE_URL below: the exported archive you are logged into.
//   2. The third @match line above: the same URL with a trailing *.
//      (@match cannot read a variable, so it has to be edited by hand.)
//
// How it works: no API token lives in this script. Pressing the button opens
// the archive in a background tab with #ingest=<VIDEO_ID>, and there the script
// types that ID into the page's own Ingest field and presses its button --
// so the request is made by the archive, with the session you are already
// logged in with. Nothing happens if you are logged out or your account is not
// in INGEST_EMAILS; the script says so instead of failing silently.

(function () {
  'use strict';

  const ARCHIVE_URL = 'https://sync.example.com/yt.html';

  // Every decision this script makes is logged under this prefix (console.log,
  // not console.info -- Firefox hides the latter unless the Info filter is on):
  // if the button
  // is missing, the console says whether the script ran at all, whether it
  // found a video ID, and where it put the button.
  const LOG = '[yt-ingest]';
  function log() {
    try {
      console.log.apply(console, [LOG].concat(Array.prototype.slice.call(arguments)));
    } catch (e) {}
  }

  const RESULT_KEY = 'ingest_result';       // cross-tab relay, archive -> YouTube
  const BUTTON_ID = 'yas-ingest-btn';
  const WAIT_MS = 20000;                    // how long to wait for the Ingest UI
  const POLL_MS = 250;

  const TEXT = {
    button: 'Zusammenfassen',
    sending: 'Wird gesendet…',
    queued: 'In die Warteschlange gestellt',
    failed: 'Fehlgeschlagen',
    notLoggedIn: 'Nicht angemeldet — im Archiv anmelden und erneut versuchen',
    noVideo: 'Keine Video-ID auf dieser Seite',
  };

  // ── Shared helpers ─────────────────────────────────────────────────────────

  function videoIdFromUrl(href) {
    // watch?v=ID, /shorts/ID, /live/ID, /embed/ID, youtu.be/ID
    let url;
    try {
      url = new URL(href);
    } catch (e) {
      return null;
    }
    const fromQuery = url.searchParams.get('v');
    if (isVideoId(fromQuery)) return fromQuery;
    const m = url.pathname.match(/^\/(?:shorts|live|embed|v)\/([^/?#]+)/);
    if (m && isVideoId(m[1])) return m[1];
    if (/(^|\.)youtu\.be$/.test(url.hostname)) {
      const id = url.pathname.slice(1).split('/')[0];
      if (isVideoId(id)) return id;
    }
    return null;
  }

  function isVideoId(id) {
    return typeof id === 'string' && /^[A-Za-z0-9_-]{11}$/.test(id);
  }

  function archiveUrlFor(videoId) {
    // The hash, not a query parameter: "?v=ID" is the archive's own
    // single-video deep link and would put the page into that view instead.
    return ARCHIVE_URL + '#ingest=' + encodeURIComponent(videoId);
  }

  function ingestIdFromHash(hash) {
    const m = String(hash || '').match(/[#&]ingest=([^&]+)/);
    if (!m) return null;
    const id = decodeURIComponent(m[1]);
    return isVideoId(id) ? id : null;
  }

  // Node can require this file for the pure helpers above; the browser parts
  // below are skipped there (no document).
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { videoIdFromUrl, isVideoId, archiveUrlFor, ingestIdFromHash };
  }
  if (typeof document === 'undefined') return;

  const onArchive = location.href.indexOf(ARCHIVE_URL) === 0;
  log('v1.1 running', location.href, onArchive ? '(archive side)' : '(youtube side)');

  // ── Part B: the archive page does the actual submitting ────────────────────

  function report(state, videoId) {
    try {
      GM_setValue(RESULT_KEY, { state: state, videoId: videoId, at: Date.now() });
    } catch (e) {}
  }

  function waitFor(test, timeoutMs) {
    return new Promise(function (resolve, reject) {
      const deadline = Date.now() + timeoutMs;
      (function poll() {
        const hit = test();
        if (hit) return resolve(hit);
        if (Date.now() > deadline) return reject(new Error('timeout'));
        setTimeout(poll, POLL_MS);
      })();
    });
  }

  function banner(text, ok) {
    let el = document.getElementById('yas-ingest-banner');
    if (!el) {
      el = document.createElement('div');
      el.id = 'yas-ingest-banner';
      el.style.cssText =
        'position:fixed;left:50%;top:1rem;transform:translateX(-50%);z-index:9999;' +
        'padding:0.6rem 1rem;border-radius:6px;font:14px/1.4 system-ui,sans-serif;' +
        'box-shadow:0 2px 12px rgba(0,0,0,0.4);color:#fff;';
      document.body.appendChild(el);
    }
    el.style.background = ok === false ? '#7f1d1d' : ok ? '#14532d' : '#1f2937';
    el.textContent = text;
  }

  function runArchiveIngest(videoId) {
    banner(TEXT.sending);
    // Drop the hash right away: a reload should not queue the video twice.
    try {
      history.replaceState(null, '', location.pathname + location.search);
    } catch (e) {}

    const visibleInput = function () {
      const input = document.getElementById('ingest-input');
      const box = document.getElementById('sync-ingest');
      if (!input || !box) return null;
      // The archive only reveals this block once /api/whoami came back with
      // can_ingest -- i.e. logged in and allowed. Until then it is display:none.
      return box.style.display === 'none' ? null : input;
    };

    waitFor(visibleInput, WAIT_MS).then(function (input) {
      const btn = document.getElementById('ingest-btn');
      input.value = videoId;
      input.dispatchEvent(new Event('input', { bubbles: true }));
      btn.click();

      // The verdict is read off the page's own mechanics, not off #sync-status:
      // that line is localised and is written by the login and sync handlers
      // too, so a status change right after the Ingest box appears may have
      // nothing to do with this submission. doIngest() instead disables the
      // button while the request is in flight, re-enables it on the response,
      // and clears the input only on 200/202.
      let sawDisabled = false;
      return waitFor(function () {
        if (btn.disabled) { sawDisabled = true; return null; }
        if (input.value === '') return { ok: true };
        // Never disabled and the field still holds the ID: doIngest() bailed
        // out before sending, which it only does on an unusable video ID.
        return sawDisabled ? { ok: false } : null;
      }, WAIT_MS).catch(function () {
        // Sent, but no verdict within the window -- a failure of this
        // submission, not the "you are not logged in" case below.
        return { ok: false };
      });
    }, function () {
      return null;  // the Ingest box never appeared: logged out, or not allowed
    }).then(function (res) {
      if (!res) {
        banner(TEXT.notLoggedIn, false);
        report('not-logged-in', videoId);
        return;
      }
      banner(res.ok ? TEXT.queued : TEXT.failed, res.ok);
      report(res.ok ? 'queued' : 'failed', videoId);
      // Chrome refuses window.close() for a tab the script did not open with
      // window.open, so this may do nothing -- the banner is the real report.
      if (res.ok) setTimeout(function () { try { window.close(); } catch (e) {} }, 1500);
    });
  }

  // ── Part A: the button on YouTube ──────────────────────────────────────────

  function styleAsYouTubeButton(btn) {
    btn.style.cssText =
      'margin-left:8px;height:36px;padding:0 16px;border:none;border-radius:18px;' +
      'background:rgba(255,255,255,0.1);color:var(--yt-spec-text-primary,#f1f1f1);' +
      'font:500 14px/36px Roboto,Arial,sans-serif;cursor:pointer;white-space:nowrap;';
  }

  function styleAsFloating(btn) {
    btn.style.cssText =
      'position:fixed;right:20px;bottom:20px;z-index:9000;height:40px;padding:0 18px;' +
      'border:none;border-radius:20px;background:#c00;color:#fff;' +
      'font:500 14px/40px Roboto,Arial,sans-serif;cursor:pointer;' +
      'box-shadow:0 2px 10px rgba(0,0,0,0.5);';
  }

  function actionRow() {
    // YouTube renames and rebuilds these containers regularly, and a container
    // that exists is not necessarily one that will show a foreign child --
    // hence both the list and the visibility check in placeButton().
    const selectors = [
      'ytd-watch-metadata #top-level-buttons-computed',
      '#actions #top-level-buttons-computed',
      '#top-level-buttons-computed',
      'ytd-watch-metadata #actions-inner #menu',
      '#above-the-fold #actions',
    ];
    for (let i = 0; i < selectors.length; i++) {
      const el = document.querySelector(selectors[i]);
      if (el) return { el: el, selector: selectors[i] };
    }
    return null;
  }

  function isVisible(el) {
    if (!el || !el.isConnected) return false;
    const box = el.getBoundingClientRect();
    return box.width > 0 && box.height > 0;
  }

  function flash(btn, text) {
    const original = btn.dataset.label || TEXT.button;
    btn.textContent = text;
    clearTimeout(btn._resetTimer);
    btn._resetTimer = setTimeout(function () { btn.textContent = original; }, 2500);
  }

  function makeButton(videoId) {
    const btn = document.createElement('button');
    btn.id = BUTTON_ID;
    btn.type = 'button';
    btn.dataset.videoId = videoId;
    btn.dataset.label = TEXT.button;
    btn.textContent = TEXT.button;
    btn.title = 'An das Archiv senden: Transkript holen und zusammenfassen';
    btn.addEventListener('click', function () {
      const id = btn.dataset.videoId;
      if (!isVideoId(id)) return flash(btn, TEXT.noVideo);
      flash(btn, TEXT.sending);
      log('queueing', id, '->', archiveUrlFor(id));
      GM_openInTab(archiveUrlFor(id), { active: false, insert: true });
    });
    return btn;
  }

  function goFloating(btn) {
    if (btn.dataset.floating === '1') return;
    btn.dataset.floating = '1';
    styleAsFloating(btn);
    document.body.appendChild(btn);
    log('button placed: floating (no usable action row)');
  }

  function placeButton() {
    const videoId = videoIdFromUrl(location.href);
    const existing = document.getElementById(BUTTON_ID);
    if (!videoId) {
      if (existing) existing.remove();
      return;
    }
    if (existing) {
      existing.dataset.videoId = videoId;
      const row = existing.dataset.floating === '1' ? null : actionRow();
      // SPA navigation rebuilds the row under us.
      if (row && existing.parentElement !== row.el) row.el.appendChild(existing);
      return;
    }

    const btn = makeButton(videoId);
    const row = actionRow();
    if (!row) return goFloating(btn);

    styleAsYouTubeButton(btn);
    row.el.appendChild(btn);
    log('button placed in', row.selector);
    // A container can accept the node and still never show it (zero-size row,
    // overflow, a re-render that drops foreign children). Verify, then fall
    // back rather than leaving the page looking untouched.
    setTimeout(function () {
      if (!isVisible(btn)) {
        log('that row did not render it; falling back to the floating button');
        goFloating(btn);
      }
    }, 800);
  }

  function watchYouTube() {
    placeButton();
    // YouTube is a single-page app: no reload between videos, and the action
    // row is rebuilt each time. The event fires on document in some versions
    // and on window in others, so listen on both.
    document.addEventListener('yt-navigate-finish', placeButton);
    window.addEventListener('yt-navigate-finish', placeButton);
    // The row often does not exist yet at document-idle, and neither event
    // fires on a cold load, so keep looking for a while.
    let tries = 0;
    const timer = setInterval(function () {
      tries += 1;
      if (tries > 40 || document.getElementById(BUTTON_ID)) {  // ~20 s
        clearInterval(timer);
        return;
      }
      placeButton();
    }, 500);
    const obs = new MutationObserver(function () {
      if (!document.getElementById(BUTTON_ID)) placeButton();
    });
    obs.observe(document.body, { childList: true, subtree: true });

    try {
      GM_addValueChangeListener(RESULT_KEY, function (key, older, newer, remote) {
        const btn = document.getElementById(BUTTON_ID);
        // Every YouTube tab runs this listener; only the one showing the video
        // that was queued should say anything.
        if (!btn || !newer || newer.videoId !== btn.dataset.videoId) return;
        flash(btn, newer.state === 'queued' ? TEXT.queued
                 : newer.state === 'failed' ? TEXT.failed
                 : TEXT.notLoggedIn);
      });
    } catch (e) {
      log('no GM_addValueChangeListener; the button will not report back', e);
    }
  }

  if (onArchive) {
    const pending = ingestIdFromHash(location.hash);
    if (pending) runArchiveIngest(pending);
  } else {
    watchYouTube();
  }
})();
