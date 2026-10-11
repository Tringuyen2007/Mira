// Minimal fake DOM for node:test. Covers what js/core and later view tests need; not a browser.
const TEXT_NODE = 3;
const ELEMENT_NODE = 1;

class FakeNode {
  constructor(nodeType) {
    this.nodeType = nodeType;
    this.parentNode = null;
  }

  remove() {
    if (this.parentNode) this.parentNode._removeChild(this);
  }

  get nextSibling() {
    if (!this.parentNode) return null;
    const siblings = this.parentNode.childNodes;
    return siblings[siblings.indexOf(this) + 1] ?? null;
  }

  get isConnected() {
    let n = this;
    while (n.parentNode) n = n.parentNode;
    return Boolean(globalThis.document && n === globalThis.document.body);
  }
}

class FakeText extends FakeNode {
  constructor(text) {
    super(TEXT_NODE);
    this.data = String(text);
  }

  get textContent() { return this.data; }
  set textContent(v) { this.data = String(v); }
}

class FakeElement extends FakeNode {
  constructor(tagName) {
    super(ELEMENT_NODE);
    this.tagName = tagName.toUpperCase();
    this.attrs = new Map();
    this.childNodes = [];
    this.listeners = new Map();
  }

  setAttribute(name, value) {
    this.attrs.set(name, String(value));
  }

  getAttribute(name) {
    return this.attrs.has(name) ? this.attrs.get(name) : null;
  }

  removeAttribute(name) {
    this.attrs.delete(name);
  }

  hasAttribute(name) {
    return this.attrs.has(name);
  }

  toggleAttribute(name, force) {
    const has = this.hasAttribute(name);
    const on = force === undefined ? !has : Boolean(force);
    if (on) this.setAttribute(name, "");
    else this.removeAttribute(name);
    return on;
  }

  get className() { return this.getAttribute("class") || ""; }
  set className(v) { this.setAttribute("class", v); }

  get classList() {
    const self = this;
    return {
      add(...names) {
        const set = new Set(self.className.split(/\s+/).filter(Boolean));
        for (const n of names) set.add(n);
        self.className = [...set].join(" ");
      },
      remove(...names) {
        const set = new Set(self.className.split(/\s+/).filter(Boolean));
        for (const n of names) set.delete(n);
        self.className = [...set].join(" ");
      },
      toggle(name, force) {
        const has = this.contains(name);
        const on = force === undefined ? !has : force;
        if (on) this.add(name); else this.remove(name);
        return on;
      },
      contains(name) {
        return new Set(self.className.split(/\s+/).filter(Boolean)).has(name);
      },
    };
  }

  get children() {
    return this.childNodes.filter((n) => n.nodeType === ELEMENT_NODE);
  }

  get firstChild() {
    return this.childNodes[0] ?? null;
  }

  get parentElement() {
    return this.parentNode && this.parentNode.nodeType === ELEMENT_NODE ? this.parentNode : null;
  }

  contains(node) {
    let n = node;
    while (n) {
      if (n === this) return true;
      n = n.parentNode;
    }
    return false;
  }

  get textContent() {
    const walk = (n) => n.nodeType === TEXT_NODE ? n.data : n.childNodes.map(walk).join("");
    return walk(this);
  }

  set textContent(v) {
    this.childNodes = [];
    if (v !== "" && v !== null && v !== undefined) {
      const t = new FakeText(v);
      t.parentNode = this;
      this.childNodes.push(t);
    }
  }

  get value() { return this._value ?? ""; }
  set value(v) { this._value = v; }

  get hidden() { return this.hasAttribute("hidden"); }
  set hidden(v) { v ? this.setAttribute("hidden", "") : this.removeAttribute("hidden"); }

  // Test-only: enough of focus()/blur() for a focus-management assertion (Esc returns focus to the trigger).
  focus() {
    this._focused = true;
    if (globalThis.document) globalThis.document.activeElement = this;
  }

  blur() {
    this._focused = false;
    if (globalThis.document && globalThis.document.activeElement === this) globalThis.document.activeElement = null;
  }

  _removeChild(child) {
    const i = this.childNodes.indexOf(child);
    if (i !== -1) this.childNodes.splice(i, 1);
    child.parentNode = null;
  }

  append(...nodes) {
    for (const n of nodes) this.appendChild(n);
  }

  appendChild(node) {
    if (node.parentNode) node.parentNode._removeChild(node);
    node.parentNode = this;
    this.childNodes.push(node);
    return node;
  }

  insertBefore(node, ref) {
    if (ref === null || ref === undefined) return this.appendChild(node);
    if (node.parentNode) node.parentNode._removeChild(node);
    node.parentNode = this;
    this.childNodes.splice(this.childNodes.indexOf(ref), 0, node);
    return node;
  }

  get children() { return this.childNodes.filter((c) => c.nodeType === ELEMENT_NODE); }

  replaceChildren(...nodes) {
    for (const c of this.childNodes) c.parentNode = null;
    this.childNodes = [];
    for (const n of nodes) this.appendChild(n);
  }

  addEventListener(type, fn) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type).add(fn);
  }

  removeEventListener(type, fn) {
    this.listeners.get(type)?.delete(fn);
  }

  // Test-only: fire listeners for `type` as if the event happened.
  dispatch(type, init) {
    const event = { type, target: this, ...init, preventDefault() {}, stopPropagation() {} };
    for (const fn of this.listeners.get(type) || []) fn(event);
    return event;
  }

  querySelector(selector) {
    return this.querySelectorAll(selector)[0] ?? null;
  }

  querySelectorAll(selector) {
    const matches = parseSelector(selector);
    const out = [];
    const walk = (n) => {
      for (const c of n.childNodes) {
        if (c.nodeType === ELEMENT_NODE) {
          if (matches(c)) out.push(c);
          walk(c);
        }
      }
    };
    walk(this);
    return out;
  }
}

// Supports: tag, .class, #id, [attr], [attr="v"] — one simple term, not a full selector grammar.
function parseSelector(selector) {
  const s = selector.trim();
  let m;
  if ((m = s.match(/^\[([a-zA-Z-]+)=(?:"([^"]*)"|'([^']*)')\]$/))) {
    const [, attr, v1, v2] = m;
    const v = v1 !== undefined ? v1 : v2;
    return (el) => el.getAttribute(attr) === v;
  }
  if ((m = s.match(/^\[([a-zA-Z-]+)\]$/))) {
    const attr = m[1];
    return (el) => el.hasAttribute(attr);
  }
  if ((m = s.match(/^#([\w-]+)$/))) {
    const id = m[1];
    return (el) => el.getAttribute("id") === id;
  }
  if ((m = s.match(/^\.([\w-]+)$/))) {
    const cls = m[1];
    return (el) => el.classList.contains(cls);
  }
  return (el) => el.tagName === s.toUpperCase();
}

class FakeDocument {
  constructor() {
    this.listeners = new Map();
    this.documentElement = new FakeElement("html");
    this.body = new FakeElement("body");
    this.activeElement = null;
  }

  createElement(tag) {
    return new FakeElement(tag);
  }

  createElementNS(_ns, tag) {
    return new FakeElement(tag);
  }

  createTextNode(text) {
    return new FakeText(text);
  }

  // Document-level listeners (e.g. a screen's own-click-outside-closes-popover handler); same shape as
  // FakeElement's, kept separate since a document isn't a node in this minimal fake.
  addEventListener(type, fn) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type).add(fn);
  }

  removeEventListener(type, fn) {
    this.listeners.get(type)?.delete(fn);
  }

  dispatch(type, init) {
    const event = { type, target: this, ...init, preventDefault() {} };
    for (const fn of this.listeners.get(type) || []) fn(event);
    return event;
  }
}

export function installFakeDom() {
  globalThis.document = new FakeDocument();
  globalThis.Node = { TEXT_NODE, ELEMENT_NODE };
}
