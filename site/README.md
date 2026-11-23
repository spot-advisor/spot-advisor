# Spot Advisor Pages source

The static bilingual landing page lives here:

- English: <code>site/index.html</code>
- 简体中文: <code>site/zh-CN/index.html</code>
- Shared styles: <code>site/assets/site.css</code>
- English and 简体中文 visual demo replays are generated from the sanitized records in <code>demo/replay*.json</code>.

## Local preview

From the repository root, run <code>python site/build.py</code>, then <code>python -m http.server 8000 --directory site/dist</code> and open <code>http://localhost:8000/</code>. The build step creates a clean, self-contained bundle containing only the HTML pages, shared CSS, hero art, and demo replays; it uses only the Python standard library.

## Publishing

<code>.github/workflows/pages.yml</code> packages <code>site/dist</code> for GitHub Pages and can only run through **Actions → Deploy GitHub Pages → Run workflow**. The workflow and site source are prepared in-repo; this repository's Pages settings have not been changed and the workflow has not been run.
