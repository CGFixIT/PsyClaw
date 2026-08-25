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

    // Every privileged mutation below can be REFUSED: 401/403 on an expired
    // session, 403 on a CSRF mismatch, 429 under the rate limit, 503 with auth
    // off. Before this they were bare `.then(reload)` -- no status check, no
    // rejection handler -- so a refusal reloaded the list from the server's
    // UNCHANGED state and the row silently snapped back. The <select> returning
    // to the old role is indistinguishable from "the change was applied and
    // then re-rendered", so an admin could believe they had demoted, deleted,
    // or reset an account when the server had rejected it outright. createUser
    // already had the right shape; these did not. Funnel them all through one
    // helper so a future mutation cannot reintroduce the silent path.
    function mutate(label, url, init) {
      return fetchFn(url, init)
        .then(function (resp) {
          if (!resp.ok) onStatus(label + " failed (" + resp.status + ")");
          return reload();
        })
        .catch(function (err) {
          // An unreachable gateway rejects before any status exists. Without
          // this the rejection escaped an event handler unhandled and nothing
          // on screen changed at all.
          onStatus(label + " failed: " + ((err && err.message) || "gateway unreachable"));
        });
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
            mutate("role change", base + "/users/" + encodeURIComponent(u.username) + "/role", {
              method: "POST",
              headers: headers(true),
              body: JSON.stringify({ role: roleSel.value }),
            });
          });
          row.appendChild(roleSel);
          const del = el("button", { type: "button", class: "toolbar-btn" }, "Delete");
          del.addEventListener("click", function () {
            if (!global.confirm("Delete " + u.username + "?")) return;
            mutate("delete", base + "/users/" + encodeURIComponent(u.username), {
              method: "DELETE",
              headers: headers(false),
            });
          });
          row.appendChild(del);
        }
        const reset = el("button", { type: "button", class: "toolbar-btn" }, "Reset password");
        reset.addEventListener("click", function () {
          const pw = global.prompt("New password for " + u.username);
          if (!pw) return;
          mutate("password reset", base + "/users/" + encodeURIComponent(u.username) + "/password", {
            method: "POST",
            headers: headers(true),
            body: JSON.stringify({ password: pw }),
          });
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
      }).catch(function (err) {
        // The one path createUser still lacked: an unreachable gateway rejects
        // before any resp exists, so the password field stayed populated and
        // the rejection escaped unhandled.
        passIn.value = "";
        onStatus("create failed: " + ((err && err.message) || "gateway unreachable"));
      });
    });

    // The initial paint is fetch-backed too -- an unreachable gateway here left
    // an empty panel plus an unhandled rejection, with no indication why.
    reload().catch(function (err) {
      onStatus("cannot load users: " + ((err && err.message) || "gateway unreachable"));
    });
  }

  global.CyClawAuthAdmin = { render: render };
})(window);
