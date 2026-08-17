// Minimal browser stubs so the export UI script can run under Node.
// Node 18+ provides atob, Blob, DecompressionStream, Response, URL natively.
// How many <option>s each <select> carries in the real template. applyLang()
// addresses them by index, so an out-of-range read here must blow up rather
// than silently write into a phantom option -- that is how index drift between
// the markup and applyLang() gets caught. Keep in sync with export.html.j2.
const __optionCounts = {'sort': 5, 'read-filter': 3, 'bookmark-filter': 2};
const __DEFAULT_OPTIONS = 4;

function __options(id) {
  const n = id in __optionCounts ? __optionCounts[id] : __DEFAULT_OPTIONS;
  const opts = [];
  for (let i = 0; i < n; i++) opts.push({textContent: '', value: ''});
  return new Proxy(opts, {
    get: function (target, prop) {
      if (typeof prop === 'string' && /^\d+$/.test(prop) && !(prop in target)) {
        throw new RangeError('option index ' + prop + ' out of range for #' + id +
                             ' (has ' + target.length + ')');
      }
      return target[prop];
    },
  });
}

const __els = {};
function __el(id) {
  if (!__els[id]) {
    __els[id] = {
      id: id,
      value: '',
      textContent: '',
      innerHTML: '',
      placeholder: '',
      disabled: false,
      hidden: false,
      style: {},
      options: __options(id),
      classList: {add: function () {}, remove: function () {}, toggle: function () {}},
      addEventListener: function () {},
      appendChild: function () {},
      setAttribute: function () {},
      getAttribute: function () { return null; },
      querySelector: function () { return __el(id + '-child'); },
      querySelectorAll: function () { return []; },
      remove: function () {},
    };
  }
  return __els[id];
}

const __store = {};
globalThis.localStorage = {
  getItem: function (k) { return k in __store ? __store[k] : null; },
  setItem: function (k, v) { __store[k] = String(v); },
  removeItem: function (k) { delete __store[k]; },
};
globalThis.document = {
  cookie: '',
  documentElement: {},
  title: '',
  getElementById: __el,
  querySelector: function () { return __el('generic'); },
  querySelectorAll: function () { return []; },
  createElement: function () { return __el('created'); },
  addEventListener: function () {},
  body: __el('body'),
};
globalThis.window = {
  location: {protocol: 'https:', hostname: 'example.com', origin: 'https://example.com',
             pathname: '/x.html', search: '', hash: '', href: 'https://example.com/x.html'},
  addEventListener: function () {},
};
globalThis.location = globalThis.window.location;
globalThis.navigator = {languages: ['de']};
globalThis.alert = function () {};
globalThis.fetch = function () { return Promise.reject(new Error('no network in tests')); };
globalThis.requestAnimationFrame = function (cb) { setTimeout(cb, 0); };
globalThis.requestIdleCallback = function (cb) { setTimeout(cb, 0); };
globalThis.history = {replaceState: function () {}};
