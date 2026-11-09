# Making a sound scheme

A sound scheme gives each of an app's events its own sound, or none. This gallery holds schemes for
QuickMail and for The Chat Place. Each app has its own events, so a scheme is made for one app.

This guide describes the form a scheme takes in this gallery, and what is checked before one is
accepted. The format every app shares is set out in
[SCHEMES.md](https://github.com/TheIdeaPlace/app-kit/blob/main/sounds/SCHEMES.md) in app-kit. Where this
guide and SCHEMES.md seem to differ, SCHEMES.md is right.

## What goes in a scheme

Each scheme is a folder in its app's `schemes/` folder:

- QuickMail: `quickmail/schemes/`
- The Chat Place: `thechatplace/schemes/`

The folder's name is the scheme's id: lowercase letters, digits and dashes, up to 40 characters, such as
`thechatplace/schemes/rain`.

```
thechatplace/schemes/rain/
  scheme.json
  sounds/
    drop.wav
    thunder.wav
  README.md        (optional, for notes about how you made it)
```

`scheme.json`:

```json
{
  "name": "Rain",
  "author": "Your name",
  "description": "One or two sentences about how it sounds.",
  "license": "CC-BY-4.0",
  "events": {
    "ReplyFinished": "custom:drop.wav",
    "TurnFailed": "custom:thunder.wav",
    "MessageSent": "builtin:softbell/send",
    "ToolRan": "none"
  }
}
```

- **name**: up to 60 characters. It must differ from every other scheme's name for the same app, ignoring
  case.
- **author**: up to 60 characters.
- **description**: optional, up to 300 characters.
- **license**: `CC0-1.0` (no conditions) or `CC-BY-4.0` (credit to the author). Only submit sounds you made
  yourself, or that you are allowed to share under that licence.
- **events**: a sound for each event you want to make one. An event you leave out plays nothing. At least
  one event needs a sound.

If you made the scheme in the app and exported it, you can use the `scheme.json` from inside the exported
file as it is. Its `format` and `app` fields are allowed, as long as `app` matches the folder.

### QuickMail's events

| Key | When it plays | Its own sound |
|---|---|---|
| `MessageSent` | A message was sent | `sent` |
| `MessageQueued` | A message was queued to send later | `queued` |
| `SendFailed` | Sending failed | `failed` |
| `NewMail` | New mail arrived in an Inbox | `arrived` |
| `WatchedMail` | Mail arrived in a conversation being watched | `attention` |
| `ActionFailed` | A delete, move, archive or empty trash failed | `error` |
| `ConnectionLost` | The connection was lost | `disconnected` |
| `ConnectionRestored` | The connection came back | `connected` |
| `CalendarReminder` | A calendar reminder | `reminder` |
| `MessageDeleted` | A message was deleted | `removed` |
| `MessageArchived` | A message was archived | `filed` |
| `MessageMoved` | A message was moved | `moved` |
| `TrashEmptied` | Trash was emptied | `emptied` |
| `RuleMatched` | A rule set to play its default sound matched | `matched` |

### The Chat Place's events

| Key | When it plays | Its own sound |
|---|---|---|
| `ToolRan` | A tool ran | `tool` |
| `FileChanged` | A file changed | `write` |
| `StillWorking` | A turn is still working | `working` |
| `MessageSent` | A message was sent | `send` |
| `MessageQueued` | A message was queued | `queue` |
| `MessageSteered` | A message was steered into the running turn | `steer` |
| `SteerTaken` | Claude took a steered message in | `steertaken` |
| `QueuedMessageSent` | A queued message went on its own | `queuesent` |
| `QueuedMessageDeleted` | You deleted a queued message | `queuedelete` |
| `TurnStopped` | You stopped a turn | `stop` |
| `ReplyFinished` | A reply finished | `reply` |
| `OtherChatFinished` | Another chat finished | `otherreply` |
| `ReplyHandedOn` | An agent conversation handed one agent's reply to the next by itself | `handon` |
| `AsideAnswered` | Ask Aside answered | `aside` |
| `NeedsYou` | A chat needs you | `needsyou` |
| `AnswerSent` | You sent an answer to a chat that needed you | `answer` |
| `TurnFailed` | A turn failed | `turnfailed` |
| `MessageNotSent` | A message couldn't be sent | `notsent` |
| `AsideNotAnswered` | Ask Aside couldn't answer | `asidefailed` |
| `ServiceTrouble` | The service is having trouble | `trouble` |
| `UsageWarning` | A usage or context warning | `warning` |
| `Compacted` | The conversation was compacted | `compacted` |
| `PeerMessage` | A message came from another session | `peer` |

The Chat Place's built-in sounds are its own, one per event (app-kit's `sounds/thechatplace/`);
none is QuickMail's. A scheme for The Chat Place uses these names.

### Sound values

- `none` plays nothing.
- `custom:<file>` plays a file in the scheme's `sounds/` folder.
- `builtin:<pack>/<sound>` plays one of the app's own sounds in a given tone:
  - Packs: `glass`, `softbell` or `wood`.
  - Sounds: the ones in the last column of that app's table above. Each app has only its own.
- `builtin:<sound>`, with no pack, plays that sound in Glass.

### Sound files

- Use WAV files only: 16-bit PCM, mono or stereo, 8 to 192 kHz. MP3 isn't accepted, because not every app
  plays it.
- Each file can be at most 5 MB and 30 seconds. Short is better: these play over speech. Most of the apps'
  own sounds are under half a second.
- Use plain file names: letters, digits, spaces, dashes and dots, ending in `.wav`.
- Every file in `sounds/` must be used by an event, and every file an event names must be there. No two
  file names may differ only in case.
- A whole scheme's sounds can be at most 80 MB, less 64 KB.

## Proposing a scheme

1. Fork this repository and add your folder under your app's `schemes/` folder. Change nothing else: a
   scheme pull request that changes any other file fails its checks.
2. Run the checks yourself, if you can:

   ```
   pip install --require-hashes -r tools/build-requirements.txt
   pip install --require-hashes --no-build-isolation -r tools/requirements.txt
   python tools/gallery.py validate
   ```

3. Open a pull request. The same checks run on it automatically.
4. Kelly listens to it and decides. Nothing is published until it is merged.

## What happens after merging

GitHub builds each scheme into a scheme file for its app: `.qmsounds` for QuickMail and `.tcpsounds` for
The Chat Place. It lists each app's files in that app's signed index, `index.signed`, with each file's size
and SHA-256, and signs that list with a key that only this repository's main branch can use. The app checks
the signature, the size and the hash before it uses anything from the gallery. It then checks every file
again, decoding included, just as it does when you import one by hand.
