(function attachRemoteStoreClient(global) {
  function cloneJson(value) {
    if (value === undefined) return undefined;
    return JSON.parse(JSON.stringify(value));
  }

  function noop() {}

  function create(config) {
    const storeId = config.storeId;
    const normalize = typeof config.normalize === "function" ? config.normalize : (value) => value;
    const onError = typeof config.onError === "function" ? config.onError : noop;
    const pollMs = Number(config.pollMs || 0);

    let applySnapshot = noop;
    let hasLocalWrites = false;
    let isSaving = false;
    let lastJson = null;
    let pollHandle = null;
    let pendingWrites = 0;
    let saveQueue = Promise.resolve();

    function snapshot(value) {
      return cloneJson(normalize(cloneJson(value)));
    }

    async function request(method, body) {
      const response = await fetch(`/api/stores/${encodeURIComponent(storeId)}`, {
        method,
        cache: "no-store",
        headers: {
          Accept: "application/json",
          ...(body ? { "Content-Type": "application/json" } : {}),
        },
        body: body ? JSON.stringify(body) : undefined,
      });

      if (response.status === 404 && method === "GET") {
        return { exists: false };
      }

      if (!response.ok) {
        throw new Error(`Remote store request failed with status ${response.status}.`);
      }

      return response.status === 204 ? { ok: true } : response.json();
    }

    async function syncFromServer(source) {
      if (pendingWrites > 0 || isSaving) return;

      try {
        const payload = await request("GET");
        if (!payload.exists) return;

        const remoteValue = snapshot(payload.value);
        const remoteJson = JSON.stringify(remoteValue);
        if (remoteJson === lastJson) return;

        const applied = applySnapshot(remoteValue, { source });
        if (applied === false) return;

        lastJson = remoteJson;
      } catch (error) {
        onError(error, source);
      }
    }

    function startPolling() {
      if (pollHandle || pollMs <= 0) return;
      pollHandle = global.setInterval(() => {
        syncFromServer("poll");
      }, pollMs);
    }

    return {
      async bootstrap(options) {
        applySnapshot = typeof options.applySnapshot === "function" ? options.applySnapshot : noop;

        const localValue = snapshot(options.getLocalSnapshot());
        const localJson = JSON.stringify(localValue);

        try {
          const payload = await request("GET");

          if (payload.exists && !hasLocalWrites) {
            const remoteValue = snapshot(payload.value);
            const remoteJson = JSON.stringify(remoteValue);
            if (remoteJson !== localJson) {
              const applied = applySnapshot(remoteValue, { source: "remote" });
              if (applied !== false) {
                lastJson = remoteJson;
              }
            } else {
              lastJson = remoteJson;
            }
          } else {
            lastJson = localJson;
            await request("PUT", { value: localValue });
          }
        } catch (error) {
          onError(error, "bootstrap");
        }

        startPolling();
      },

      queueSave(value) {
        const localValue = snapshot(value);
        lastJson = JSON.stringify(localValue);
        hasLocalWrites = true;
        pendingWrites += 1;

        saveQueue = saveQueue
          .catch(() => {})
          .then(() => {
            isSaving = true;
            return request("PUT", { value: localValue });
          })
          .catch((error) => {
            onError(error, "save");
          })
          .finally(() => {
            pendingWrites = Math.max(0, pendingWrites - 1);
            isSaving = pendingWrites > 0;
          });

        return saveQueue;
      },

      syncNow() {
        return syncFromServer("sync");
      },
    };
  }

  global.RemoteStoreClient = { create };
})(window);
