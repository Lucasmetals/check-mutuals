# Check Mutuals

Find Instagram accounts you follow that do not follow you back.

The script supports two modes:

- Interactive browser mode, using a saved Playwright browser session.
- Offline export mode, using Instagram's official data export files.

## Setup

Install the Python dependency and Playwright browser:

```powershell
py -m pip install -r requirements.txt
py -m playwright install chromium
```

## Browser Mode

Run the script:

```powershell
py instagram_non_mutuals.py
```

Then enter the Instagram username when prompted.

On the first run, a browser opens so you can log in. The login session is saved in `.instagram_browser_profile/`, so later runs can reuse it.

To write the result to CSV:

```powershell
py instagram_non_mutuals.py --csv non_mutuals.csv
```

## Export Mode

You can also use Instagram's official "Download your information" export:

```powershell
py instagram_non_mutuals.py --export-dir path\to\instagram-export
```

Or provide the files directly:

```powershell
py instagram_non_mutuals.py --followers followers_1.json --following following.json
```

## Output

The output only includes accounts you follow that do not follow you back.
