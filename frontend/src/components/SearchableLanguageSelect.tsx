import { useState } from "react";
import { ChevronDown, Search } from "lucide-react";

/**
 * 字幕语言多选（可搜索）。
 *
 * 两处约束改之前先读 [ai/ui/003](../../../ai/ui/003-language-trigger-label-overflows-the-page.md)：
 *
 * 1. 标签里 join 的是**语言代码**（`en` / `zh-Hans` / `pt-BR`），项数无上界 —— 样式里因此
 *    **不允许**给 `.select-trigger span` 加 `white-space: nowrap`：nowrap 之下没有断行机会，
 *    `overflow-wrap` 与 `min-width: 0` 都救不回来，标签的 min-content 会等于整段文字，
 *    一路把右栏轨道和整页顶宽（12 种语言实测 84px）。
 * 2. 下拉列表只列**该视频真的有的**语言，而选择值可能来自设置里的默认值 —— 于是存在
 *    「标签里有一个下拉里没有复选框、界面上摘不掉」的语言（已知未处理，见 ai/ui/README.md）。
 */
export function SearchableLanguageSelect({
  languages,
  selectedLanguages,
  onChange
}: {
  languages: string[];
  selectedLanguages: string[];
  onChange: (languages: string[]) => void;
}) {
  const [isOpen, setIsOpen] = useState(false);
  const [query, setQuery] = useState("");
  const filteredLanguages = languages.filter((language) => language.toLowerCase().includes(query.trim().toLowerCase()));
  const selectedSet = new Set(selectedLanguages);
  const summary = selectedLanguages.length ? `已选 ${selectedLanguages.length} 项：${selectedLanguages.join(", ")}` : "选择字幕语言";

  function toggleLanguage(language: string) {
    const next = new Set(selectedLanguages);
    if (next.has(language)) next.delete(language);
    else next.add(language);
    onChange(Array.from(next));
  }

  return (
    <div className="field language-select">
      <span>字幕语言</span>
      <button
        type="button"
        className="select-trigger"
        aria-expanded={isOpen}
        onClick={() => setIsOpen((current) => !current)}
      >
        <span>{summary}</span>
        <ChevronDown size={17} />
      </button>
      {isOpen && (
        <div className="language-dropdown">
          <label className="search-field">
            <Search size={16} />
            <input
              aria-label="搜索字幕语言"
              placeholder="搜索字幕语言"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </label>
          <div className="language-options" role="listbox" aria-label="字幕语言列表" aria-multiselectable="true">
            {filteredLanguages.length ? (
              filteredLanguages.map((language) => (
                <label key={language} className="language-option">
                  <input
                    aria-label={`字幕 ${language}`}
                    type="checkbox"
                    checked={selectedSet.has(language)}
                    onChange={() => toggleLanguage(language)}
                  />
                  <span>{language}</span>
                </label>
              ))
            ) : (
              <p className="empty-option">没有匹配的字幕语言</p>
            )}
          </div>
        </div>
      )}
      {!languages.length && <span className="hint">解析后显示可用字幕</span>}
    </div>
  );
}
