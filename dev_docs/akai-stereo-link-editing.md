# Stereo-linked sample editing in the Akai editor (S1000 / S2000 / S3000)

Research and plan, written 2026-10-22. **Analysis only: nothing is built, and nothing here was measured on hardware.** It comes from reading
`src/ui/program_editor_window.py`, `src/core/program_editor_bridge.py`, `src/ui/slice_editor_window.py`, `src/ui/yamaha_samples_tab.py` and
`src/core/akai_sample_file.py`. Statements that are the author's recollection rather than something in the repo are marked **(unverified)**.

## The idea

A stereo recording on these samplers is two mono samples, `NAME-L` and `NAME-R`. Today the Samples tab lists them as two rows and every edit
(markers, loop, root note, trim, filter, ...) is done twice by hand. The owner's preference is **one row** in the left-hand list for a linked
pair, shown as a single stereo sample, with edits applied to both halves together.

## Verdict

**Feasible, no protocol blockers.** Every operation is something the app already does to one mono sample; a pair means doing it twice with the
same arguments. The cost is (1) the UI assumption that list row == sample index and (2) making a two-sample send safe.

## What exists that we can reuse

- **Slice Editor is already stereo-capable.** `SliceEditorWindow(extra_channels=[right], channel_suffixes=("-L", "-R"))` analyses the mono mix,
  exports `<slice>-L` / `<slice>-R` and `_create_program_from_slices(stereo=True)` builds the two-zone keygroups (left zone 1, right zone 2,
  `VPANO1` -50 / `VPANO2` +50). Slicing a linked pair needs only the two audio buffers handed in. (The pan values are unverified on hardware, see
  AGENTS.md "ReCycle .rx2 import".)
- **Stereo display precedent:** the Yamaha Samples tab stacks two `WaveformView`s at half height and draws them as one display
  (`set_stack_position`, `waveform_view_right`). `SliceWaveformView.set_waveform(..., right=)` draws a second lane too.
- **Marker and header edits are per-index writes** (`_schedule_write(field, "sample", value, index=...)`); mirroring is a second call.
- **Yamaha working copy keeps a channel list** (`_Staged.channels`), a model for a multi-channel staged edit.

## What is hard

### 1. Row == sample index is assumed everywhere

- `sample_list_widget.currentRow()` is used 48 times in `program_editor_window.py`; `_sample_name_at_row(row)` returns `self._sample_list[row]`;
  `_sample_waveform_cache` is keyed by index; `_on_samples_loaded` builds one row widget per name and queues one `submit_sample_length(i)` per
  sample; `_sample_row_labels` / `_sample_row_dots` are keyed by row.
- `_staged_edits` is keyed by NAME (good), the cache by INDEX.
- **Approach for the single-row list:** keep `_sample_list` flat (it is the sampler's truth and the zone combos, rename/delete and the bridge all
  use it). Add a display model on top: an ordered list of entries `{label, members: [index_L, index_R] | [index]}`. A row maps to its members;
  selection resolves to `primary` (L) and `partner` (R) indices. Everything that talks to the sampler keeps using flat indices. Every one of the
  48 `currentRow()` sites has to be classified: "the selected entry" (use members) versus "a sampler index" (use the primary).
- Zone combos and programs keep listing `-L` and `-R` separately: that is how the hardware works, and Create-from-slices already writes them so.

### 2. Pairing is a name heuristic

- `SSPAIR` (header offset 136, 2 bytes, "Address of stereo partner (internal use)", `s3k.params`) is **never read by the app** (not in
  `_SAMPLE_DETAIL_FIELDS`) and is written as zero on `.s1`/`.s3` export. It is a block address; nothing in the repo maps a block address to a
  list index (`SLOCAT` is a WAVE memory address, a different thing). So pairing has to be by name: `X-L` with `X-R`, the same rule the app uses
  when it creates pairs.
- The sampler's own stereo recordings use the same `-L`/`-R` names **(unverified)**.
- **Link only when safe:** same base name, both halves present, equal `SLNGTH` and `SSRATE`. Otherwise show two ordinary rows. Offer an
  Unlink. Orphan `-L`, unrelated samples with matching names and unequal lengths are all "not a pair".
- Unknown: whether the sampler itself treats an app-SENT pair as linked (SSPAIR would be 0 or stale for those). See the probe below.

### 3. Sends are two long transfers (the real risk)

- Send to Sampler is send as `<name>-TMP` -> confirm resident -> delete original -> rename back (`_perform_sample_edit_real`), each half an SDS
  dump that can take minutes. A pair does this twice.
- Need an explicit answer for L ok / R failed: roll back by deleting the L `-TMP` and keeping both originals, or leave both originals and say
  so. Never delete an original before its replacement is confirmed resident (existing rule); the pair rule should be: **delete neither original
  until both replacements are resident**, then delete both, then rename both.
- A new sample takes the LOWEST FREE slot (measured), so find each by name, exactly once, as today. After a replace the two halves may not be
  adjacent in the sampler's list; the display model groups by name so that does not matter.
- The mono working-copy send is itself **not tried on hardware** (AGENTS.md); stereo sits on that unproven base.

## Feature by feature

| Feature | Effort | Notes |
|---|---|---|
| One row, stacked stereo waveform | Medium | The row/index refactor is the bulk. Audio loads twice (two SDS dumps), so a pair takes about twice as long to show. |
| Start/end, loop start/end | Small-medium | Mirror `SSTART`/`SMPEND`/`LOOPAT1`/`LLNGTH1` to both indices. The debounce keys are per-field today; they need the index in the key. Verify both halves after the write. |
| Root note, tune, loop type, hold | Small | Mirror the header fields. `STUNO` baseline is per-sample `SBANDW`: both halves are usually the same bucket, but compute per half (`_tuning_for_resent_sample` rule). |
| Slicing | Small | Existing stereo path (see above). |
| Rename, duplicate, delete | Small-medium | Rename edits the base name and keeps the suffix (base is at most 10 characters to leave room for `-L`/`-R`, `channel_suffixes`). Duplicate and delete act on both. Name-clash checks on both new names. |
| Trim, reverse, fade | Medium | `_StagedEdit` holds one buffer. Stage both halves in lockstep with identical parameters. Undo memory doubles (about 88 KB per second of mono 44.1 kHz per step per half). |
| Filter | Medium | Same parameters on both. |
| Normalise | Medium | Must use one gain from the combined peak, or the stereo image shifts. |
| Stretch / Pitch | Medium | Deterministic, so identical parameters give identical lengths. |
| Reverb | Medium | The prototype is mono and the tail length depends on the signal: pad both to the same length. Result is the same reverb on both sides, not a wide one. |
| Send / replace | Large | See risk 3. |
| Edit in External App | Medium | "Load Edited Audio" averages stereo to mono today; it would have to split a stereo WAV back into two halves. |
| Save / Load Akai File | Small | Two `.s3`/`.s1` files per pair (the file format is one sample). |
| Detect Root Note, previews | Small | Analyse the L half or the mix. Previews are mono unless `SlicePreviewPlayer` is taught stereo. |
| Keygroup filter, envelopes, zone settings | None | Both zones already share the keygroup; this is not a sample-level link. |

## Suggested order

0. **Read-only probe (owner's S2000, app closed, no DAW).** A small tool in the style of `tools/s2000_sample_check.py`: for a
   sampler-recorded stereo pair and an app-sent pair, print name, `SLNGTH`, `SSRATE`, `SBANDW`, `SSPAIR` and the markers. Answers: do real
   pairs use `-L`/`-R`, are lengths equal, does the sampler set `SSPAIR` for app-sent pairs. Read-only; keep to header reads of a few samples.
1. **Link layer: pairing + one row + stacked waveform + mirrored live edits** (markers, loop, root, tune, loop type, hold). This alone delivers
   "edit loop points on a stereo sample once".
2. **Slicing from a pair.**
3. **Working-copy edits, then the paired send** with the both-resident-before-delete rule above.
4. Rename/duplicate/delete of pairs can come with step 1 (they are small and the list needs them to feel complete).

## Alternative considered (cheaper, not preferred)

Keep the list flat: detect the partner, bracket the two rows visually, and mirror edits to the partner behind a "Link L/R" toggle. It avoids the
row == index refactor but keeps two rows. The owner prefers the single row (better experience), so the plan above targets that; this stays the
fallback if the refactor proves too invasive.

## Open questions for the owner

- Display name of a pair in the list: `NAME` with a stereo badge, or `NAME-L/R`?
- If one half has been edited or unlinked, is a lone-half row acceptable, or should an unlinked pair always show both rows?
- Should an Unlink / "Link as stereo" be manual only, or also auto-detected on every list load (default: auto, with a manual override)?
- Reverb on a pair: same on both sides (simple) or decorrelated (more work, a prototype change)?

## Test notes for later

`FakeS1000` and `DemoBridge` samples are all mono; the demo needs a `-L`/`-R` pair (and an orphan, and an unequal-length lookalike) before any
of this can be tested without hardware. Window tests use the real `BridgeWorker` thread and `wait_until_idle()` (see `TESTING.md`).
