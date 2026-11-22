/**
 * @fileoverview Shared DOM test setup: jest-dom matchers and cleanup.
 */
import '@testing-library/jest-dom/vitest';
import {cleanup} from '@testing-library/react';
import {afterEach} from 'vitest';

afterEach(() => {
  cleanup();
});
