import './LanguageIndicator.css';
import type { LanguageInfo } from '../../types';

interface LanguageIndicatorProps {
  language: LanguageInfo | null;
}

export function LanguageIndicator({ language }: LanguageIndicatorProps) {
  if (!language) return null;
  return (
    <div className="lang-indicator" title={`Detected language: ${language.name}`} aria-label={`Language: ${language.name}`}>
      <span className="lang-indicator__code">{language.code.toUpperCase()}</span>
      <span className="lang-indicator__name">{language.name}</span>
    </div>
  );
}
