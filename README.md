# The Idea Place sound gallery

Sound schemes for The Idea Place apps, one folder per app. Each app has its own events, so each has its
own schemes:

| App | Folder | Published at | Scheme files |
|---|---|---|---|
| [QuickMail](https://github.com/kellylford/QuickMail) | `quickmail/schemes/` | https://theideaplace.github.io/soundgallery/quickmail/ | `.qmsounds` |
| [The Chat Place](https://github.com/TheIdeaPlace/AIChat) | `thechatplace/schemes/` | https://theideaplace.github.io/soundgallery/thechatplace/ | `.tcpsounds` |

A scheme gives an app's events (new mail or a reply finished, a message sent, the connection dropping, and
so on) a set of sounds. The apps share one format for schemes and one set of rules for the gallery. They
are set out in [SCHEMES.md](https://github.com/TheIdeaPlace/app-kit/blob/main/sounds/SCHEMES.md) in
app-kit, and the checks here use app-kit's own code, so the gallery and the apps agree.

## Getting a scheme

- **QuickMail:** open Settings, choose the Sounds tab and then **Browse schemes…**. Pick a scheme and choose
  **Add**. It then appears under Sound scheme, ready to preview, and is kept when you save Settings.
- **The Chat Place:** open the File menu, then Sounds, then **Browse Gallery**. Pick a scheme and choose
  **Add**.

You can also download a scheme file from the app's gallery page and import it in the same place.

## Making one

See [CONTRIBUTING.md](CONTRIBUTING.md). Anyone can propose a scheme by pull request. Kelly listens to each one
before it is merged and published.

## How the gallery is kept safe

The apps treat everything from here as untrusted:

1. **On every pull request**, `tools/gallery.py validate` checks each scheme: file names, sizes, lengths, and
   that each sound is a WAV file every app can play. It also checks that every value names one of that
   app's events and sounds. A scheme pull request may change only files in scheme folders, and its checks
   run the main branch's tools and tests, never code the pull request brings.
2. **On main only**, the gallery is built, and each app's index is signed into `index.signed` by a separate
   job that holds the key. The signing key is held in the `gallery-signing` environment, which admits only
   `main`. Before a scheme file is listed, the build reads it back with the app's own checks.
3. **An app refuses an index whose signature doesn't verify** against the key built into it, and an index
   that names another app. One key signs every app's index, so each index says which app it is for.
4. **An app downloads only the files its index names**, from this site only, and **refuses a file whose
   size or SHA-256 differs**.
5. **The app then imports the file with the same checks as importing one by hand**, decoding each sound
   before using it.

## Licences

Each scheme carries its own licence, either CC0-1.0 or CC-BY-4.0, given in its `scheme.json`. The tools in
`tools/` are under the MIT licence.
