# Runbook: Rotate a key (planned or after a leak)

1. **Azure OpenAI:** Azure portal, the OpenAI resource, Keys and Endpoint, regenerate the key that is not in use. Update `.env` (or Key Vault). Then regenerate the other one.
2. **Neo4j AuraDB:** Aura console, instance, reset password. Update `.env`.
3. **GitHub token:** github.com, Settings, Developer settings, Fine-grained tokens, regenerate. Keep scope to the one repo, Contents read-only. Update `.env`.
4. Run `pytest` and one small pipeline run to confirm everything still connects.
5. **If it was a leak:** check `gold.llm_call_log` and Azure cost for calls you did not make. If the key was pushed to GitHub, rotating is the fix; rewriting history is not enough on its own because the key is already exposed.
6. Record it in the exceptions log in docs/00 if anything was done out of process.
