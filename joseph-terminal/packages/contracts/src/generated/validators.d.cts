/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * Validates a canonical workspace using precompiled JSON Schema rules.
 * @param value Untrusted JSON value.
 * @return Whether every required workspace field and constraint is valid.
 */
export function workspaceValid(value: unknown): boolean;

/**
 * Validates a canonical drawing using precompiled JSON Schema rules.
 * @param value Untrusted JSON value.
 * @return Whether every required drawing field and constraint is valid.
 */
export function drawingValid(value: unknown): boolean;
