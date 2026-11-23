# DMHT Lyrics Library

Separate private app for two people. Search, save, edit, delete and back up lyrics; import available text lyrics from Muktisukh and Dada Bhagwan FM; send selected lyrics to an open song editor in DMHT Bhakti Studio. This is a separate repository, not a replacement for Studio.

## 1. Deploy the private library on Render

1. Create a NEW **private** GitHub repository and upload the contents of the library ZIP. The ZIP contains licensed lyric text; do not make the repository public. Do not upload it to your existing Bhakti Studio repository.
2. Create a Render Blueprint from that repository. `render.yaml` uses a Starter web service with a 1 GB persistent disk; check Render's current price before creating it. Keep one instance and one worker. The disk survives restarts, but is not a backup.
3. Set two distinct usernames in Render environment variables `OWNER_USER` and `HOST_USER`. Generate a different strong password hash for each account on your computer: install Python, then run `pip install werkzeug` followed by `python -c "from werkzeug.security import generate_password_hash; print(generate_password_hash(input('Password: ')))"`. Set the resulting hashes as `OWNER_PASSWORD_HASH` and `HOST_PASSWORD_HASH` respectively. Do not put passwords or hashes in GitHub or chat. The blueprint generates `SECRET_KEY` and sets `LYRICS_DB` to the persistent disk.
4. Deploy and test each login separately. Missing password hashes prevent login. To revoke access, change that account's password hash AND rotate `SECRET_KEY` to invalidate all existing sessions. Sessions expire after eight hours. Both users can edit, import and delete.

The built-in login throttle is basic and per process. It is not a replacement for a managed identity provider or network-level rate limiting. Do not host sensitive personal data. Keep regular backups with Download backup; the file contains private library data. Restore merges entries and skips matches, not a full reset. Source import uses a background thread: a restart interrupts it; rerun to add missing records. Use one worker only.

## 2. Import collections

The app includes 2,483 Gujarati, Roman/English and Hindi lyric entries collected from Muktisukh, plus 89 available entries from Dada Bhagwan FM; it adds these to the database automatically on startup without overwriting matching imported entries. They are based on available data, not a guarantee that every Pad is included. To look for more, on the library home page choose **Import collections**, then run each import separately. Wait for status Finished before starting the other. Muktisukh entries are saved separately for Gujarati, English/Roman and Hindi when provided. Dada Bhagwan FM imports available lyric text discovered through its sitemap and public website endpoints. Missing files, inaccessible tracks, unlisted albums, non-text formats, unsupported languages and upstream changes can result in omissions. Review failed and skipped counts; the app does not guarantee a complete copy of either site. Re-running adds missing items without overwriting your edited imported entries. Duplicates across websites or albums may occur. Verify lyrics and titles before your show. Imports may take a long time.

FM also offers an album-by-album preview before saving. Manual Add lyrics works for approved sources. Search matches titles, alternate spellings and text; it also tries word order and simple Roman spelling variations. Gujarati and Roman spellings work when present. Results are capped at 500; narrow the search if needed.

## 3. Connect to Studio

This requires the SEPARATE Studio integration ZIP. Back up your Studio repository first. Copy the integration ZIP files into the existing Studio repository, preserving paths: `index.html`, `lyrics-bridge.js` (new), and `buzzer/server.py`. These are based on the latest Roman Gujarati Studio version. If you have made other changes since that version, do not overwrite them blindly; merge carefully. Redeploy Studio.

Open a song editor in Studio, click Find in Lyrics Library, enter the HTTPS address of this Render library and allow the popup. Sign in, search for a Pad and open it. Select Send to open Studio song. Switch back to Studio and confirm the replacement, review and save the song. The replacement only affects the open song editor, not the projector automatically. Copy and paste remains an alternative. One line of text becomes one lyric step in Studio; Studio removes empty lines. No media is transferred.

## Testing locally

`pip install -r requirements.txt`; set `SECRET_KEY` (random, at least 32 characters), both username/hash pairs, `LYRICS_DB` to a local file and `COOKIE_SECURE=0`; run `flask --app app run`. Use HTTPS in deployment. Never set COOKIE_SECURE=0 on Render.

FM collection note: in our 2026 test, 743 unique FM track links were attempted; 654 failed or timed out in this environment. The 89 available entries are not the full FM collection. Each song should be reviewed against its source. The two websites can change their data or access patterns.

## Language filter and alternative sites

Use the language / script selector beside Search: Gujarati script, Hindi / Devanagari script, English / Roman script, or All. Roman script may contain Gujarati or Hindi words written with English letters; it does not mean the lyrics have been translated into English. Labels are inferred from source data or the text's script, not guaranteed correct. Open an entry and select the correct label to change it. The library does not automatically translate or transliterate lyrics. Backups preserve corrected labels; old backups are classified when restored.

Search JSS Pads and PDFs uses the JSS website's live search and shows up to its first 15 results, with links to available PDF files. It does not download or store JSS lyrics. Some PDFs use legacy Gujarati fonts that extract as unreadable text; do not rely on copy/paste without review. Source search depends on the site remaining available and may break if its format changes.

In Import collections, Try Satsang catalogue discovers tracks through the Satsang site catalogue and attempts available lyric text files. Satsang and Dadabhagwan FM often reference the same files on download.dadabhagwan.org; Satsang is not necessarily a different file host and cannot fix an unavailable shared file. Import attempts can take a long time and results are not guaranteed complete. Existing FM records are skipped rather than overwritten. The tested FM seed remains 89 entries; this update does not pre-populate all Satsang or JSS lyrics.
