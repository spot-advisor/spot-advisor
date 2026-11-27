const chinese = document.documentElement.lang === "zh-CN";
const status = document.getElementById("copy-status");
const heroCopy = document.getElementById("hero-copy");

function syncInstallCommand() {
  const host = document.querySelector('input[name="host"]:checked').value;
  heroCopy.dataset.copy = "install-" + host;
  const name = host === "codex" ? "Codex" : "Claude Code";
  heroCopy.setAttribute("aria-label", chinese ? "复制 " + name + " 安装命令" : "Copy " + name + " install command");
}

if (navigator.clipboard?.writeText) {
  document.getElementById("install-fallback").hidden = true;
  document.querySelector(".host-choice").addEventListener("change", syncInstallCommand);
  window.addEventListener("pageshow", syncInstallCommand);
  syncInstallCommand();

  for (const button of document.querySelectorAll("button[data-copy]")) {
    const label = button.textContent;
    button.hidden = false;
    button.addEventListener("click", async () => {
      const command = document.getElementById(button.dataset.copy);
      try {
        await navigator.clipboard.writeText(command.textContent);
        button.textContent = chinese ? "已复制" : "Copied";
        status.textContent = chinese ? "已复制到剪贴板。" : "Copied to clipboard.";
      } catch {
        command.scrollIntoView({ block: "center" });
        const range = document.createRange();
        range.selectNodeContents(command);
        const selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        button.textContent = chinese ? "请手动复制" : "Copy manually";
        status.textContent = chinese ? "文本已选中，请使用系统的复制快捷键。" : "Text selected. Use your system’s copy shortcut.";
      }
    });
    button.addEventListener("blur", () => {
      button.textContent = label;
      status.textContent = "";
    });
  }
}
