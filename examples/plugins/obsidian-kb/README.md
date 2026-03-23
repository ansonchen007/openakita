# obsidian-kb

Example RAG plugin: scans a Markdown vault, exposes `obsidian_search`, registers an Obsidian retrieval source, and adds an `on_retrieve` hook to inject short excerpts into context.

Set `vault_path` in the plugin config before use.
