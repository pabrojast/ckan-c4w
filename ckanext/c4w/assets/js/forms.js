/* Shared, progressively enhanced C4W forms. No third-party widget dependencies. */
(function () {
  'use strict';
  function init() {
    const messages = JSON.parse(document.getElementById('c4w-form-messages')?.textContent || '{}');
    const t = key => messages[key] || key;
    const fold = text => text.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
    document.querySelectorAll('[data-picker]').forEach(root => {
      const select = root.querySelector('select');
      const enhanced = root.querySelector('.c4w-picker__enhanced');
      const input = enhanced.querySelector('input');
      const list = enhanced.querySelector('ul');
      const dropdown = enhanced.querySelector('.c4w-picker__dropdown');
      const status = enhanced.querySelector('[role=status]');
      const chips = enhanced.querySelector('.c4w-picker__selected');
      const required = select.required;
      // Native validation must focus the visible search field after enhancement.
      select.required = false;
      let timer, controller, index = -1, matches = [];
      select.hidden = true; enhanced.hidden = false;
      root.querySelector('label').htmlFor = input.id = select.id + '-search';
      input.setAttribute('aria-describedby', select.getAttribute('aria-describedby') || '');
      const close = () => { dropdown.hidden = true; input.setAttribute('aria-expanded', 'false'); input.removeAttribute('aria-activedescendant'); };
      function sync() {
        input.setCustomValidity(required && !select.value ? t('Choose an option from the results.') : '');
        chips.replaceChildren();
        Array.from(select.selectedOptions).filter(o => o.value).forEach(option => {
          const chip = document.createElement('span'); chip.className = 'c4w-picker__chip';
          const label = document.createElement('span'); label.textContent = option.textContent;
          const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = '×';
          remove.setAttribute('aria-label', root.dataset.remove + ' ' + option.textContent);
          remove.addEventListener('click', () => {
            option.selected = false;
            if (!select.multiple) select.value = '';
            select.dispatchEvent(new Event('change', {bubbles: true})); sync(); input.focus();
          });
          chip.append(label, remove); chips.append(chip);
        });
      }
      function choose(row) {
        let option = Array.from(select.options).find(o => o.value === row.id);
        if (!option) { option = new Option(row.title, row.id); select.add(option); }
        if (!select.multiple) select.value = row.id;
        option.selected = true; input.value = ''; sync(); close();
        select.dispatchEvent(new Event('change', {bubbles: true})); input.focus();
      }
      function highlight(next) {
        index = next;
        Array.from(list.children).forEach((li, i) => li.setAttribute('aria-selected', String(i === index)));
        if (list.children[index]) {
          input.setAttribute('aria-activedescendant', list.children[index].id);
          list.children[index].scrollIntoView({block: 'nearest'});
        }
      }
      async function search() {
        if (controller) controller.abort();
        controller = new AbortController(); index = -1; matches = []; list.replaceChildren();
        input.removeAttribute('aria-activedescendant'); dropdown.hidden = false;
        input.setAttribute('aria-expanded', 'true'); status.textContent = root.dataset.loading;
        try {
          const url = new URL(root.dataset.url, location.origin); url.searchParams.set('q', input.value);
          const response = await fetch(url, {signal: controller.signal, credentials: 'same-origin'});
          if (!response.ok) throw new Error('lookup');
          const body = await response.json(); matches = body.results;
          matches.forEach((row, i) => {
            const li = document.createElement('li'); li.id = list.id + '-' + i; li.setAttribute('role', 'option');
            li.textContent = row.title; li.setAttribute('aria-selected', 'false');
            li.addEventListener('mousedown', event => event.preventDefault());
            li.addEventListener('click', () => choose(row)); list.append(li);
          });
          status.textContent = matches.length ? t('Results') + ': ' + matches.length + (body.more ? '+' : '') : root.dataset.empty;
        } catch (error) { if (error.name !== 'AbortError') status.textContent = root.dataset.error; }
      }
      input.addEventListener('input', () => { clearTimeout(timer); if (controller) controller.abort(); timer = setTimeout(search, 220); });
      input.addEventListener('focus', search);
      input.addEventListener('keydown', event => {
        if (event.key === 'Escape') close();
        if (['ArrowDown', 'ArrowUp'].includes(event.key)) {
          event.preventDefault(); if (dropdown.hidden) search();
          if (matches.length) highlight((index + (event.key === 'ArrowDown' ? 1 : -1) + matches.length) % matches.length);
        }
        if (event.key === 'Enter') { event.preventDefault(); if (matches[index]) choose(matches[index]); }
      });
      root.addEventListener('focusout', () => setTimeout(() => { if (!root.contains(document.activeElement)) close(); }, 0));
      document.addEventListener('click', event => { if (!root.contains(event.target)) close(); });
      sync();
    });
    const register = document.getElementById('c4w-register-form');
    if (register) {
      const radios = Array.from(register.querySelectorAll('[name=org_choice]'));
      function organisationMode() {
        const selected = radios.find(r => r.checked);
        ['existing', 'new'].forEach(mode => {
          const group = register.querySelector('[data-org-' + mode + ']');
          if (!group) return;
          group.hidden = selected.value !== mode;
          group.querySelectorAll('input,select,textarea').forEach(control => { control.disabled = group.hidden; });
        });
      }
      radios.forEach(radio => radio.addEventListener('change', organisationMode));
      if (radios.length) {
        organisationMode();
        [['[data-new-org]', 'new'], ['[data-existing-org]', 'existing']].forEach(([selector, value]) => {
          register.querySelector(selector).addEventListener('click', () => {
            radios.find(r => r.value === value).checked = true; organisationMode();
            const group = register.querySelector('[data-org-' + value + ']');
            group.querySelector('input:not([type=hidden]):not([disabled])').focus();
          });
        });
        const requested = register.querySelector('[name=org_name_requested]');
        const similar = register.querySelector('[data-org-similar]'); let timer, generation = 0;
        requested.addEventListener('input', () => {
          clearTimeout(timer); const current = ++generation;
          timer = setTimeout(async () => {
            if (!requested.value.trim()) { similar.textContent = ''; return; }
            const root = register.querySelector('[data-picker]');
            try {
              const url = new URL(root.dataset.url, location.origin); url.searchParams.set('q', requested.value);
              const response = await fetch(url); if (!response.ok) return;
              const data = await response.json(); if (current !== generation) return;
              similar.textContent = data.results.length ? t('Similar organisations:') + ' ' + data.results.slice(0, 3).map(r => r.title).join('; ') : '';
            } catch (_) { similar.textContent = ''; }
          }, 250);
        });
      }
      register.querySelectorAll('input[type=password]').forEach(input => {
        const wrap = document.createElement('div'); wrap.className = 'c4w-password';
        input.before(wrap); wrap.append(input);
        const toggle = document.createElement('button'); toggle.type = 'button'; toggle.textContent = t('Show');
        toggle.setAttribute('aria-label', t('Show password')); toggle.setAttribute('aria-pressed', 'false');
        toggle.addEventListener('click', () => {
          const show = input.type === 'password'; input.type = show ? 'text' : 'password';
          toggle.textContent = show ? t('Hide') : t('Show'); toggle.setAttribute('aria-label', show ? t('Hide password') : t('Show password')); toggle.setAttribute('aria-pressed', String(show));
        }); wrap.append(toggle);
      });
      register.addEventListener('submit', event => {
        if (!register.reportValidity()) { event.preventDefault(); event.stopImmediatePropagation(); }
      }, true);
    }
    document.querySelectorAll('[data-conditional]').forEach(root => {
      const control = document.querySelector('[name="' + root.dataset.conditional + '"]');
      const refresh = () => { root.hidden = !control.checked; };
      control.addEventListener('change', refresh); refresh();
    });
    document.querySelectorAll('[data-rich-editor]').forEach(root => {
      const textarea = root.querySelector('textarea');
      const editor = document.createElement('div'); editor.className = 'c4w-rich-editor';
      editor.contentEditable = 'true'; editor.setAttribute('role', 'textbox'); editor.setAttribute('aria-multiline', 'true');
      editor.setAttribute('aria-label', root.querySelector('label').textContent); editor.innerHTML = textarea.value;
      const toolbar = document.createElement('div'); toolbar.className = 'c4w-rich-toolbar';
      [['bold', 'B', 'Bold'], ['italic', 'I', 'Italic'], ['insertUnorderedList', '• List', 'Bullet list']].forEach(([command, label, title]) => {
        const button = document.createElement('button'); button.type = 'button'; button.textContent = label; button.setAttribute('aria-label', t(title));
        button.addEventListener('mousedown', e => e.preventDefault());
        button.addEventListener('click', () => { editor.focus(); document.execCommand(command); textarea.value = editor.innerHTML; });
        toolbar.append(button);
      });
      textarea.hidden = true; textarea.after(toolbar, editor);
      editor.addEventListener('input', () => { textarea.value = editor.innerHTML; });
      editor.addEventListener('paste', event => { event.preventDefault(); document.execCommand('insertText', false, event.clipboardData.getData('text/plain')); });
    });
    document.querySelectorAll('[data-filter-select]').forEach(root => {
      const select = root.querySelector('select'), input = root.querySelector('input');
      const list = document.createElement('div'); list.className = 'c4w-filter-options';
      Array.from(select.options).forEach(option => {
        const label = document.createElement('label'), check = document.createElement('input');
        check.type = 'checkbox'; check.checked = option.selected;
        check.addEventListener('change', () => { option.selected = check.checked; });
        label.append(check, document.createTextNode(option.textContent)); list.append(label);
      });
      select.hidden = true; select.after(list);
      root.querySelector('.c4w-field__hint').hidden = true;
      input.addEventListener('input', () => list.querySelectorAll('label').forEach(label => { label.hidden = !fold(label.textContent).includes(fold(input.value)); }));
    });
    document.querySelectorAll('[data-image-crop]').forEach(root => {
      const input = root.querySelector('input[type=file]'), img = root.querySelector('img');
      const sliders = root.querySelectorAll('input[type=range]'); let url;
      img.style.aspectRatio = root.dataset.ratio;
      function position() { img.style.objectPosition = `${Number(sliders[0].value) * 100}% ${Number(sliders[1].value) * 100}%`; }
      sliders.forEach(s => s.addEventListener('input', position));
      input.addEventListener('change', () => {
        if (!input.files[0]) return;
        if (url) URL.revokeObjectURL(url); url = URL.createObjectURL(input.files[0]);
        img.src = url; img.hidden = false; root.querySelector('.c4w-crop-controls').hidden = false; position();
      });
    });
    document.querySelectorAll('[data-project-form]').forEach(form => {
      let dirty = false, leaving = false;
      form.addEventListener('input', () => { dirty = true; });
      form.addEventListener('change', () => { dirty = true; });
      form.addEventListener('submit', () => { leaving = true; });
      window.addEventListener('beforeunload', event => { if (dirty && !leaving) { event.preventDefault(); event.returnValue = ''; } });
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
