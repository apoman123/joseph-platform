/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview The licence header every hand-written source file in
 * joseph-terminal starts with, shared by the ESLint and stylelint checks.
 *
 * The header comes first in the file (after a shebang line, if any) and is
 * followed by one blank line, as the Google TypeScript and JavaScript guides
 * order copyright information before `@fileoverview` and imports.
 */

/** Copyright line of the header. */
export const COPYRIGHT_LINE = 'Copyright 2026 Joseph Terminal contributors';

/** SPDX licence line of the header. */
export const SPDX_LINE = 'SPDX-License-Identifier: MIT';

/**
 * Header for TypeScript and JavaScript files: a JSDoc `@license` block, as
 * the TypeScript guide asks for copyright information in a JSDoc.
 */
export const SCRIPT_HEADER = [
  '/**',
  ' * @license',
  ` * ${COPYRIGHT_LINE}`,
  ` * ${SPDX_LINE}`,
  ' */',
].join('\n');

/** Header for CSS files: a plain block comment (CSS has no JSDoc). */
export const CSS_HEADER = [
  '/*',
  ` * ${COPYRIGHT_LINE}`,
  ` * ${SPDX_LINE}`,
  ' */',
].join('\n');

/**
 * Describes how a file's start deviates from the expected header.
 * @param {string} text Full file text, with any byte order mark removed.
 * @param {string} header Expected header (`SCRIPT_HEADER` or `CSS_HEADER`).
 * @return {{offset: number, problem: string|null}} `offset` is where the
 *     header must start (after a shebang line, if any); `problem` is null when
 *     the header is present and followed by a blank line or the end of file,
 *     otherwise a message that says what is wrong.
 */
export function checkHeader(text, header) {
  const offset = text.startsWith('#!') ? text.indexOf('\n') + 1 : 0;
  const rest = text.slice(offset);
  if (!rest.startsWith(header)) {
    return {
      offset,
      problem: `File must start with the licence header:\n${header}`,
    };
  }
  const after = rest.slice(header.length);
  if (after === '' || after === '\n' || after.startsWith('\n\n')) {
    return {offset, problem: null};
  }
  return {
    offset,
    problem: 'Separate the licence header from the code by one blank line.',
  };
}

/**
 * Returns the file text with the header inserted or repaired.
 *
 * A leading comment that already carries `@license` or an SPDX identifier is
 * replaced; otherwise the header is inserted before the existing first line.
 * @param {string} text Full file text, with any byte order mark removed.
 * @param {string} header Expected header (`SCRIPT_HEADER` or `CSS_HEADER`).
 * @return {string} The fixed text.
 */
export function fixHeader(text, header) {
  const {offset} = checkHeader(text, header);
  const before = text.slice(0, offset);
  let rest = text.slice(offset);
  const leading = /^\/\*[\s\S]*?\*\/[ \t]*\n?/.exec(rest);
  if (leading && /@license|SPDX-License-Identifier/.test(leading[0])) {
    rest = rest.slice(leading[0].length);
  }
  rest = rest.replace(/^\n+/, '');
  return `${before}${header}\n${rest === '' ? '' : '\n'}${rest}`;
}
