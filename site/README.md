# Spot Advisor official Pages source

The static bilingual landing page lives here:

- English: <code>site/index.html</code>
- 简体中文: <code>site/zh-CN/index.html</code>
- Shared styles: <code>site/assets/site.css</code>
- English and 简体中文 visual demo replays are generated from the sanitized records in <code>demo/replay*.json</code>.

## Local preview

From the repository root, run <code>python site/build.py</code>, then <code>python -m http.server 8000 --directory site/dist</code> and open <code>http://localhost:8000/</code>. The standard-library build renders both pages using the [formal metrics, manifest, and per-task records](../eval/results/spot24-release/formal). It also copies the shared CSS, hero art, and demo replays.

## Official publishing

The official site is [https://spot-advisor.github.io/](https://spot-advisor.github.io/), served by the organization repository [spot-advisor/spot-advisor.github.io](https://github.com/spot-advisor/spot-advisor.github.io). Its workflow clones this public release repository's <code>main</code> branch, runs <code>python site/build.py</code>, and deploys only <code>site/dist</code>. It refreshes daily and can also be run manually from the Pages repository's **Actions → Publish official site → Run workflow** page.
