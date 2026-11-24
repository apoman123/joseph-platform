/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Project ESLint rules for Google style points that no
 * published rule checks: the licence header, the source file layout, comment
 * syntax, braces on loops, the TODO form and kebab-case file names. Loaded by
 * `eslint.config.mjs` as `local/*`.
 */

import path from 'node:path';

import {SCRIPT_HEADER, checkHeader, fixHeader} from './license-header.mjs';

/** Base URL of the Google TypeScript style guide, for rule docs. */
const TS_GUIDE = 'https://google.github.io/styleguide/tsguide.html';

/**
 * Comment bodies that are tool directives rather than prose; they keep the
 * block form their tool requires.
 */
const DIRECTIVE =
  /^\s*(?:eslint(?:[\s-]|$)|globals?\s|exported\s|istanbul\s|c8\s|v8\s|@vite-ignore|webpack[A-Z]|[#@]__(?:PURE|NO_SIDE_EFFECTS)__)/;

/** JSDoc tags that make a block file-level or tool metadata, not API docs. */
const FILE_LEVEL_TAGS =
  /@(?:license|fileoverview|file|typedef|callback|vitest-environment|jest-environment|jsx|jsxImportSource|jsxRuntime|jsxFrag)\b/;

/** Node types a JSDoc block may document. */
const DOCUMENTABLE = new Set([
  'AccessorProperty',
  'ClassDeclaration',
  'ExportAllDeclaration',
  'ExportDefaultDeclaration',
  'ExportNamedDeclaration',
  'FunctionDeclaration',
  'MethodDefinition',
  'Property',
  'PropertyDefinition',
  'TSAbstractAccessorProperty',
  'TSAbstractMethodDefinition',
  'TSAbstractPropertyDefinition',
  'TSCallSignatureDeclaration',
  'TSConstructSignatureDeclaration',
  'TSDeclareFunction',
  'TSEnumDeclaration',
  'TSEnumMember',
  'TSIndexSignature',
  'TSInterfaceDeclaration',
  'TSMethodSignature',
  'TSModuleDeclaration',
  'TSParameterProperty',
  'TSPropertySignature',
  'TSTypeAliasDeclaration',
  'VariableDeclaration',
]);

/** Function-like initialisers that make a local variable a named function. */
const FUNCTION_LIKE = new Set([
  'ArrowFunctionExpression',
  'ClassExpression',
  'FunctionExpression',
]);

/** Google's TODO form: `TODO: <link or owner> - <what>`. */
const TODO_FORM = /^\s*(?:\*\s*)?TODO: \S+(?: \S+)* - \S/;

/** Kebab-case file stem (the part of the name before the first dot). */
const KEBAB_STEM = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

/**
 * Whether a comment is the only thing on the lines it spans.
 * @param {import('eslint').SourceCode} sourceCode Source being linted.
 * @param {import('estree').Comment} comment Comment to test.
 * @return {boolean} True when no code shares its first or last line.
 */
function isStandalone(sourceCode, comment) {
  const {start, end} = comment.loc;
  const before = sourceCode.lines[start.line - 1].slice(0, start.column);
  const after = sourceCode.lines[end.line - 1].slice(end.column);
  return before.trim() === '' && after.trim() === '';
}

/**
 * Whether a comment is a JSX child comment (`{/* ... *\/}`), the only comment
 * syntax JSX allows between elements.
 * @param {import('eslint').SourceCode} sourceCode Source being linted.
 * @param {import('estree').Comment} comment Comment to test.
 * @return {boolean} True for a comment inside an empty JSX expression.
 */
function isJsxChildComment(sourceCode, comment) {
  const node = sourceCode.getNodeByRangeIndex(comment.range[0]);
  return node?.type === 'JSXEmptyExpression';
}

/**
 * Rewrites a standalone block comment as consecutive `//` lines.
 * @param {import('eslint').SourceCode} sourceCode Source being linted.
 * @param {import('estree').Comment} comment Block comment to rewrite.
 * @return {string} Replacement text for the comment's range.
 */
function toLineComments(sourceCode, comment) {
  const {start} = comment.loc;
  const indent = sourceCode.lines[start.line - 1].slice(0, start.column);
  const lines = comment.value.split('\n').map(line => {
    const starred = /^\s*\* ?(.*)$/.exec(line);
    return (starred ? starred[1] : line.trim()).trimEnd();
  });
  while (lines.length > 0 && lines[0] === '') {
    lines.shift();
  }
  while (lines.length > 0 && lines[lines.length - 1] === '') {
    lines.pop();
  }
  return lines
    .map(line => (line === '' ? '//' : `// ${line}`))
    .join(`\n${indent}`);
}

/**
 * Lists the nodes that start at a token.
 * @param {import('eslint').SourceCode} sourceCode Source being linted.
 * @param {import('eslint').AST.Token} token First token after a comment.
 * @return {Array<import('estree').Node>} Nodes that start at the token,
 *     innermost first.
 */
function nodesStartingAt(sourceCode, token) {
  const chain = [];
  let node = sourceCode.getNodeByRangeIndex(token.range[0]);
  while (node && node.range[0] === token.range[0]) {
    chain.push(node);
    node = node.parent;
  }
  return chain;
}

/**
 * Whether a node is a CommonJS export assignment (`module.exports = ...`).
 * @param {import('estree').Node} node Candidate statement.
 * @return {boolean} True for `module.exports = ...` or `exports.x = ...`.
 */
function isCommonJsExport(node) {
  if (node.type !== 'ExpressionStatement') return false;
  const expression = node.expression;
  if (expression.type !== 'AssignmentExpression') return false;
  const left = expression.left;
  if (left.type !== 'MemberExpression') return false;
  const object = left.object;
  return (
    (object.type === 'Identifier' && object.name === 'exports') ||
    (object.type === 'Identifier' &&
      object.name === 'module' &&
      left.property.type === 'Identifier' &&
      left.property.name === 'exports')
  );
}

/**
 * Whether a variable declaration lives inside a function (a local).
 * @param {import('estree').Node} node A VariableDeclaration.
 * @return {boolean} True when an enclosing function exists.
 */
function isLocal(node) {
  for (let parent = node.parent; parent; parent = parent.parent) {
    if (
      parent.type === 'FunctionDeclaration' ||
      parent.type === 'FunctionExpression' ||
      parent.type === 'ArrowFunctionExpression'
    ) {
      return true;
    }
  }
  return false;
}

/** Requires the project licence header at the top of every file. */
const licenseHeader = {
  meta: {
    type: 'layout',
    fixable: 'code',
    docs: {
      description: 'Require the joseph-terminal licence header first.',
      url: `${TS_GUIDE}#copyright-information`,
    },
    schema: [],
    messages: {problem: '{{problem}}'},
  },
  create(context) {
    const sourceCode = context.sourceCode;
    return {
      Program() {
        const text = sourceCode.text;
        const {problem} = checkHeader(text, SCRIPT_HEADER);
        if (problem === null) return;
        context.report({
          loc: {line: 1, column: 0},
          messageId: 'problem',
          data: {problem},
          fix: fixer =>
            fixer.replaceTextRange(
              [0, text.length],
              fixHeader(text, SCRIPT_HEADER)
            ),
        });
      },
    };
  },
};

/**
 * Enforces Google's comment syntax: `//` for implementation comments
 * (multi-line ones as consecutive `//` lines) and JSDoc only on the
 * declarations and members it documents.
 */
const commentStyle = {
  meta: {
    type: 'suggestion',
    fixable: 'code',
    docs: {
      description: 'Use // for implementation comments and JSDoc for docs.',
      url: `${TS_GUIDE}#jsdoc-versus-comments`,
    },
    schema: [],
    messages: {
      blockComment:
        'Implementation comments use `//` lines, not `/* */` (tsguide ' +
        '"Multi-line comments"); `/* */` is only for inline comments ' +
        'such as `/* name= */` and JSX children.',
      jsdocNotDocs:
        'JSDoc (`/** */`) documents a declaration or member; this one ' +
        'precedes a statement, so use `//` (tsguide "JSDoc versus comments").',
      jsdocOnLocal:
        'A local variable is implementation detail: comment it with `//`, ' +
        'not JSDoc (tsguide "JSDoc versus comments").',
    },
  },
  create(context) {
    const sourceCode = context.sourceCode;
    return {
      Program() {
        for (const comment of sourceCode.getAllComments()) {
          if (comment.type !== 'Block') continue;
          const isJsdoc = comment.value.startsWith('*');
          if (!isJsdoc) {
            if (DIRECTIVE.test(comment.value)) continue;
            if (isJsxChildComment(sourceCode, comment)) continue;
            const multiLine = comment.loc.start.line !== comment.loc.end.line;
            const standalone = isStandalone(sourceCode, comment);
            if (!multiLine && !standalone) continue;
            context.report({
              loc: comment.loc,
              messageId: 'blockComment',
              fix: standalone
                ? fixer =>
                    fixer.replaceTextRange(
                      comment.range,
                      toLineComments(sourceCode, comment)
                    )
                : null,
            });
            continue;
          }
          if (!isStandalone(sourceCode, comment)) continue;
          if (FILE_LEVEL_TAGS.test(comment.value)) continue;
          const token = sourceCode.getTokenAfter(comment, {
            includeComments: false,
          });
          const chain = token ? nodesStartingAt(sourceCode, token) : [];
          const target = chain.find(
            node => DOCUMENTABLE.has(node.type) || isCommonJsExport(node)
          );
          if (!target) {
            context.report({loc: comment.loc, messageId: 'jsdocNotDocs'});
          } else if (
            target.type === 'VariableDeclaration' &&
            isLocal(target) &&
            !target.declarations.some(
              declarator =>
                declarator.init && FUNCTION_LIKE.has(declarator.init.type)
            )
          ) {
            context.report({loc: comment.loc, messageId: 'jsdocOnLocal'});
          }
        }
      },
    };
  },
};

/** Requires TODOs in Google's `TODO: <link or owner> - <what>` form. */
const todoFormat = {
  meta: {
    type: 'suggestion',
    docs: {
      description: 'Require TODO: <link or owner> - <what>.',
      url: 'https://google.github.io/styleguide/pyguide.html#312-todo-comments',
    },
    schema: [],
    messages: {
      todo: 'Write TODOs as `TODO: <link or owner> - <what>`.',
      marker: 'Use a `TODO: <link or owner> - <what>` comment, not {{marker}}.',
    },
  },
  create(context) {
    const sourceCode = context.sourceCode;
    return {
      Program() {
        for (const comment of sourceCode.getAllComments()) {
          // Only a marker that starts a comment line is a TODO; prose that
          // mentions the word is not.
          for (const line of comment.value.split('\n')) {
            const marker = /^\s*(?:\*\s*)?(FIXME|XXX|HACK)\b/.exec(line);
            if (marker) {
              context.report({
                loc: comment.loc,
                messageId: 'marker',
                data: {marker: marker[1]},
              });
            } else if (
              /^\s*(?:\*\s*)?TODO\b/.test(line) &&
              !TODO_FORM.test(line)
            ) {
              context.report({loc: comment.loc, messageId: 'todo'});
            }
          }
        }
      },
    };
  },
};

/**
 * Whether the line after a node or comment is blank (or the file ends).
 * @param {import('eslint').SourceCode} sourceCode Source being linted.
 * @param {number} line 1-based line the node or comment ends on.
 * @return {boolean} True when line + 1 is empty or past the end.
 */
function isFollowedByBlankLine(sourceCode, line) {
  const next = sourceCode.lines[line];
  return next === undefined || next.trim() === '';
}

/**
 * Whether a statement may sit among the imports: a directive such as
 * `'use client'` (which Next.js requires before the imports) or a re-export.
 * @param {import('estree').Node} statement Top-level statement.
 * @return {boolean} True for directives and `export ... from` statements.
 */
function isPreamble(statement) {
  if (statement.type === 'ExpressionStatement') {
    return Boolean(statement.directive);
  }
  return (
    (statement.type === 'ExportNamedDeclaration' ||
      statement.type === 'ExportAllDeclaration') &&
    statement.source != null
  );
}

/**
 * Enforces the Google file layout: licence, `@fileoverview`, imports and
 * implementation, in that order, each section followed by a blank line.
 */
const fileStructure = {
  meta: {
    type: 'layout',
    fixable: 'whitespace',
    docs: {
      description: 'Require the Google source file structure.',
      url: `${TS_GUIDE}#source-file-structure`,
    },
    schema: [],
    messages: {
      afterFileoverview:
        'Separate the @fileoverview JSDoc from what follows by a blank line ' +
        '(tsguide "Source file structure").',
      afterImports:
        'Separate the imports from the implementation by a blank line ' +
        '(tsguide "Source file structure").',
      importsFirst:
        'Imports come before the implementation (tsguide "Source file ' +
        'structure").',
    },
  },
  create(context) {
    const sourceCode = context.sourceCode;
    return {
      Program(program) {
        const overview = sourceCode
          .getAllComments()
          .find(
            comment =>
              comment.type === 'Block' &&
              /^\*\s*\n?\s*\*?\s*@(?:fileoverview|file)\b/.test(comment.value)
          );
        if (
          overview &&
          !isFollowedByBlankLine(sourceCode, overview.loc.end.line)
        ) {
          context.report({
            loc: overview.loc,
            messageId: 'afterFileoverview',
            fix: fixer => fixer.insertTextAfterRange(overview.range, '\n'),
          });
        }
        let seenCode = false;
        let lastImport = null;
        for (const statement of program.body) {
          if (statement.type === 'ImportDeclaration') {
            if (seenCode) {
              context.report({node: statement, messageId: 'importsFirst'});
            }
            lastImport = statement;
          } else if (!isPreamble(statement)) {
            seenCode = true;
          }
        }
        if (
          lastImport &&
          lastImport !== program.body[program.body.length - 1] &&
          !isFollowedByBlankLine(sourceCode, lastImport.loc.end.line)
        ) {
          context.report({
            node: lastImport,
            messageId: 'afterImports',
            fix: fixer => fixer.insertTextAfter(lastImport, '\n'),
          });
        }
      },
    };
  },
};

/**
 * Requires braces around every loop body (tsguide "Control flow statements
 * and blocks": only an `if` that fits on one line may omit them; ESLint's
 * `curly` rule covers the `if` and multi-line cases).
 */
const loopBraces = {
  meta: {
    type: 'suggestion',
    fixable: 'code',
    docs: {
      description: 'Require a braced block as every loop body.',
      url: `${TS_GUIDE}#control-flow-statements-and-blocks`,
    },
    schema: [],
    messages: {
      braces:
        'Loops always use a braced block (tsguide "Control flow statements ' +
        'and blocks").',
    },
  },
  create(context) {
    /**
     * Reports a loop whose body is a bare statement.
     * @param {import('estree').Node} node A loop statement.
     */
    function check(node) {
      if (node.body.type === 'BlockStatement') return;
      context.report({
        node: node.body,
        messageId: 'braces',
        fix: fixer => [
          fixer.insertTextBefore(node.body, '{'),
          fixer.insertTextAfter(node.body, '}'),
        ],
      });
    }
    return {
      DoWhileStatement: check,
      ForInStatement: check,
      ForOfStatement: check,
      ForStatement: check,
      WhileStatement: check,
    };
  },
};

/** Tags whose text is a free-form block that Google does not indent. */
const UNINDENTED_TAGS = new Set([
  '@desc',
  '@example',
  '@fileoverview',
  '@file',
  '@license',
]);

/**
 * Requires wrapped block-tag text (`@param`, `@return`, ...) to be indented
 * four spaces (tsguide "Line wrapping"). Text lined up with the description
 * on the tag line is also accepted, as the guide allows (but discourages) it.
 */
const jsdocTagIndent = {
  meta: {
    type: 'layout',
    fixable: 'whitespace',
    docs: {
      description: 'Indent wrapped JSDoc block-tag text by four spaces.',
      url: `${TS_GUIDE}#line-wrapping`,
    },
    schema: [],
    messages: {
      indent:
        'Indent the wrapped text of {{tag}} by four spaces (tsguide "Line ' +
        'wrapping").',
    },
  },
  create(context) {
    const sourceCode = context.sourceCode;
    return {
      Program() {
        for (const comment of sourceCode.getAllComments()) {
          if (comment.type !== 'Block' || !comment.value.startsWith('*')) {
            continue;
          }
          let tag = null;
          let alignColumn = -1;
          let offset = comment.range[0] + 2;
          for (const line of comment.value.split('\n')) {
            const lineStart = offset;
            offset += line.length + 1;
            const match = /^(\s*\* ?)(\s*)(.*)$/.exec(line);
            if (!match) continue;
            const [, prefix, indent, text] = match;
            if (text === '' || text === '/') {
              tag = null;
              continue;
            }
            if (text.startsWith('@')) {
              tag = text.split(/\s/)[0];
              const named = /^@(?:arg|argument|param|property)$/.test(tag);
              const description = (
                named
                  ? /^(@\S+\s+(?:\{[^}]*\}\s+)?\S+\s+)/
                  : /^(@\S+\s+(?:\{[^}]*\}\s+)?)/
              ).exec(text);
              alignColumn = description ? description[1].length : -1;
              continue;
            }
            if (tag === null || UNINDENTED_TAGS.has(tag)) continue;
            if (indent.length === 4 || indent.length === alignColumn) continue;
            const start = lineStart + prefix.length;
            context.report({
              loc: {
                start: sourceCode.getLocFromIndex(start),
                end: sourceCode.getLocFromIndex(start + indent.length),
              },
              messageId: 'indent',
              data: {tag},
              fix: fixer =>
                fixer.replaceTextRange([start, start + indent.length], '    '),
            });
          }
        }
      },
    };
  },
};

/** Requires kebab-case file names (the project's file naming convention). */
const filenameCase = {
  meta: {
    type: 'suggestion',
    docs: {
      description: 'Require kebab-case file names.',
      url: 'https://google.github.io/styleguide/jsguide.html#file-name',
    },
    schema: [],
    messages: {
      name: 'File names are kebab-case (lowercase words joined by `-`): {{name}}.',
    },
  },
  create(context) {
    return {
      Program(node) {
        const name = path.basename(context.filename);
        const stem = name.replace(/^\./, '').split('.')[0];
        if (KEBAB_STEM.test(stem)) return;
        context.report({node, messageId: 'name', data: {name}});
      },
    };
  },
};

/** The `local` ESLint plugin. */
export const localPlugin = {
  meta: {name: 'eslint-plugin-local'},
  rules: {
    'comment-style': commentStyle,
    'file-structure': fileStructure,
    'filename-case': filenameCase,
    'jsdoc-tag-indent': jsdocTagIndent,
    'license-header': licenseHeader,
    'loop-braces': loopBraces,
    'todo-format': todoFormat,
  },
};
