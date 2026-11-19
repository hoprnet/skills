<!-- Copy this section into a consuming repository's root AGENTS.md to make
     shared HOPR skills discoverable without a client plugin installation. -->

## Shared HOPR skills

Before HOPR-specific work, provision the shared skills from
[hoprnet/skills](https://github.com/hoprnet/skills). A fresh agent must do this
without waiting for a separate installation request. Reuse a sibling `skills`
checkout, or set `HOPR_SKILLS_DIR` to an existing checkout. From this repository's
root, clone only when that location is absent:

```sh
hopr_skills_dir="${HOPR_SKILLS_DIR:-../skills}"
if [ ! -e "$hopr_skills_dir" ] && [ ! -L "$hopr_skills_dir" ]; then
  git clone --depth 1 https://github.com/hoprnet/skills.git "$hopr_skills_dir"
fi
```

Preserve existing checkouts, including local edits; do not automatically pull or
reset them. Verify the required `SKILL.md` exists, then read it directly for the
current task, even if the agent's skill list has not discovered it:

| Task | File relative to the skills checkout |
|---|---|
| Protocol behavior, architecture, routing, tickets, sessions or PIX semantics | `plugins/hopr/skills/concepts/SKILL.md` |
| Diagnosing HOPR client, entry, relay or exit incidents from captured evidence | `plugins/hopr/skills/debug/SKILL.md` |
| Crate releases, dependency repins, backports or cross-repository release cascades | `plugins/hopr/skills/release/SKILL.md` |

Read the selected skill's referenced resources as needed. Direct file loading
works for this task without a preinstalled plugin; client-native installation is
documented in the skills repository's [README](https://github.com/hoprnet/skills#install-and-use).
If cloning or reading is blocked, report the missing skill and continue only work
that does not depend on it.
