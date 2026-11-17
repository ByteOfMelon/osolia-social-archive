# Osolia Social archive

Static, read-only archive of the Mastodon server **social.osolia.xyz** (Jan–Jul 2025),
served by GitHub Pages from `docs/` at https://social.osolia.xyz.

Only public posts and the accounts that were actually used are included.

## Rebuilding

The inputs are not in this repo (they live in the private server backup):

- `data/archive-export.json`: made on the old server by `tools/export_archive.rb`
  (`bin/rails runner` inside the Mastodon web container)
- `media/`: the server's `public/system` folder, without `cache/`

With those in place:

```
python build.py
```

Stdlib-only Python; output goes to `docs/`.
