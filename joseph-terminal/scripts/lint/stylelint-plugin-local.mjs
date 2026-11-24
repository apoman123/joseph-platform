/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Project stylelint rule `local/license-header`: every
 * hand-written stylesheet starts with the licence header block comment.
 */

import stylelint from 'stylelint';

import {
  COPYRIGHT_LINE,
  CSS_HEADER,
  SPDX_LINE,
  checkHeader,
} from './license-header.mjs';

const {
  createPlugin,
  utils: {report, ruleMessages, validateOptions},
} = stylelint;

/** Name the rule is configured under. */
const RULE_NAME = 'local/license-header';

/** Rule messages. */
const messages = ruleMessages(RULE_NAME, {
  rejected: problem => problem,
});

/**
 * Checks (and with `--fix` repairs) the licence header of a stylesheet.
 * @param {boolean} primary `true` to enable the rule.
 * @return {import('stylelint').RuleBase} The PostCSS root visitor.
 */
function licenseHeaderRule(primary) {
  return (root, result) => {
    const valid = validateOptions(result, RULE_NAME, {
      actual: primary,
      possible: [true],
    });
    if (!valid) return;
    const text = root.source?.input.css ?? '';
    const {problem} = checkHeader(text.replace(/^\uFEFF/, ''), CSS_HEADER);
    if (problem === null) return;
    report({
      ruleName: RULE_NAME,
      result,
      node: root,
      start: {line: 1, column: 1},
      end: {line: 1, column: 2},
      message: messages.rejected(problem),
      fix: () => {
        const first = root.first;
        if (
          first?.type === 'comment' &&
          /@license|SPDX-License-Identifier/.test(first.text)
        ) {
          first.remove();
        }
        const next = root.first;
        root.prepend({
          text: `* ${COPYRIGHT_LINE}\n * ${SPDX_LINE}`,
          raws: {before: '', left: '\n ', right: '\n '},
        });
        root.first.raws.before = '';
        if (next) next.raws.before = '\n\n';
      },
    });
  };
}

licenseHeaderRule.ruleName = RULE_NAME;
licenseHeaderRule.messages = messages;
licenseHeaderRule.meta = {
  url: 'https://google.github.io/styleguide/htmlcssguide.html#comments',
  fixable: true,
};

/** The `local/license-header` stylelint plugin. */
export const licenseHeaderPlugin = createPlugin(RULE_NAME, licenseHeaderRule);
