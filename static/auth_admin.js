/* Shared Users panel for terminal.html and harness.html. No inline script. */
(function (global) {
  function el(tag, attrs, text) {
    const node = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function (k) { node.setAttribute(k, attrs[k]); });
    if (text != null) node.textContent = text;
    return node;
  }

  function render(root, opts) {
    const base = opts.base || "/auth";
    const fetchFn = opts.fetchFn;
    const getCsrf = opts.getCsrf || function () { return ""; };
    const actorRole = opts.actorRole || "operator";
    const onStatus = opts.onStatus || function () {};

    root.textContent = "";
    const listBox = el("div", { id: "authUsersList" });
    const form = el("div", { id: "authUsersForm" });
    const userIn = el("input", { id: "authNewUser", type: "text", placeholder: "username" });
    const passIn = el("input", { id: "authNewPass", type: "password", placeholder: "password" });
    const roleIn = el("select", { id: "authNewRole" });
    const roles = actorRole === "admin" ? ["operator", "audit", "admin"] : ["operator", "audit"];
    roles.forEach(function (r) { roleIn.appendChild(el("option", { value: r }, r)); });
    const createBtn = el("button", { id: "authCreateUserBtn", type: "button", class: "toolbar-btn" }, "Create user");
    form.appendChild(userIn);
    form.appendChild(passIn);
    form.appendChild(roleIn);
    form.appendChild(createBtn);
    root.appendChild(form);
    root.appendChild(listBox);

    function headers(json) {
      const h = {};
      if (json) h["Content-Type"] = "application/json";
      const csrf = getCsrf();
      if (csrf) h["X-CyClaw-CSRF"] = csrf;
      return h;
    }

    async function reload() {
      const resp = await fetchFn(base + "/users", { method: "GET" });
      if (resp.status === 503) {
        listBox.textContent = "authentication is off";
        onStatus("auth disabled");
        return;
      }
      if (!resp.ok) {
        listBox.textContent = "cannot list users (" + resp.status + ")";
        return;
      }
      const users = await resp.json();
      listBox.textContent = "";
      users.forEach(function (u) {
        const row = el("div", { class: "auth-user-row" });
        row.appendChild(el("span", null, u.username + " · " + u.role + (u.disabled ? " · disabled" : "")));
        if (actorRole === "admin") {
          const roleSel = el("select");
          ["admin", "operator", "audit"].forEach(function (r) {
            const opt = el("option", { value: r }, r);
            if (r === u.role) opt.selected = true;
            roleSel.appendChild(opt);
          });
          roleSel.addEventListener("change", function () {
            fetchFn(base + "/users/" + encodeURIComponent(u.username) + "/role", {
              method: "POST",
              headers: headers(true),
              body: JSON.stringify({ role: roleSel.value }),
            }).then(reload);
          });
          row.appendChild(roleSel);
          const del = el("button", { type: "button", class: "toolbar-btn" }, "Delete");
          del.addEventListener("click", function () {
            if (!global.confirm("Delete " + u.username + "?")) return;
            fetchFn(base + "/users/" + encodeURIComponent(u.username), {
              method: "DELETE",
              headers: headers(false),
            }).then(reload);
          });
          row.appendChild(del);
        }
        const reset = el("button", { type: "button", class: "toolbar-btn" }, "Reset password");
        reset.addEventListener("click", function () {
          const pw = global.prompt("New password for " + u.username);
          if (!pw) return;
          fetchFn(base + "/users/" + encodeURIComponent(u.username) + "/password", {
            method: "POST",
            headers: headers(true),
            body: JSON.stringify({ password: pw }),
          }).then(reload);
        });
        row.appendChild(reset);
        listBox.appendChild(row);
      });
    }

    createBtn.addEventListener("click", function () {
      fetchFn(base + "/users", {
        method: "POST",
        headers: headers(true),
        body: JSON.stringify({
          username: userIn.value.trim(),
          password: passIn.value,
          role: roleIn.value,
        }),
      }).then(function (resp) {
        passIn.value = "";
        if (!resp.ok) onStatus("create failed " + resp.status);
        reload();
      });
    });

    reload();
  }

  global.CyClawAuthAdmin = { render: render };
})(window);
