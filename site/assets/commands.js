const chinese = document.documentElement.lang === "zh-CN";
const status = document.getElementById("copy-status");

if (navigator.clipboard?.writeText) {
  for (const button of document.querySelectorAll("button[data-copy]")) {
    const command = document.getElementById(button.dataset.copy);
    const label = button.textContent;
    button.hidden = false;
    button.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(command.textContent);
        button.textContent = chinese ? "已复制" : "Copied";
        status.textContent = chinese ? "命令已复制到剪贴板。" : "Command copied to clipboard.";
      } catch {
        const range = document.createRange();
        range.selectNodeContents(command);
        const selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        button.textContent = chinese ? "请手动复制" : "Copy manually";
        status.textContent = chinese ? "命令已选中，请使用系统的复制快捷键。" : "Command selected. Use your system’s copy shortcut.";
      }
    });
    button.addEventListener("blur", () => {
      button.textContent = label;
      status.textContent = "";
    });
  }
}
