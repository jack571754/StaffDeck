// @vitest-environment jsdom

import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import SqlEditor, { formatSql } from './SqlEditor';

describe('SqlEditor', () => {
  afterEach(() => {
    cleanup();
  });
  it('renders title, line numbers, and highlighted tokens for SQL', () => {
    const onChange = vi.fn();
    const sql = 'SELECT * FROM orders WHERE date = :date';

    const { container } = render(
      <SqlEditor queryType="sql" queryContent={sql} onChange={onChange} />
    );

    // Title and badge
    expect(screen.getByText('SQL 查询')).toBeDefined();
    expect(screen.getByText('SQL')).toBeDefined();

    // Line number gutter (1 line)
    expect(screen.getByText('1')).toBeDefined();

    // Check syntax token elements
    const keywords = container.querySelectorAll('.code-token.keyword');
    expect(keywords.length).toBeGreaterThanOrEqual(3); // SELECT, FROM, WHERE
    const keywordTexts = Array.from(keywords).map((k) => k.textContent);
    expect(keywordTexts).toContain('SELECT');
    expect(keywordTexts).toContain('FROM');
    expect(keywordTexts).toContain('WHERE');

    // Check parameter token (:date)
    const propertyTokens = container.querySelectorAll('.code-token.property');
    const propertyTexts = Array.from(propertyTokens).map((p) => p.textContent);
    expect(propertyTexts).toContain(':date');

    // Check detected parameter badge in footer and highlighted token in code
    expect(screen.getAllByText(':date').length).toBe(2);
  });

  it('calls onChange when user types in textarea', () => {
    const onChange = vi.fn();
    render(<SqlEditor queryType="sql" queryContent="" onChange={onChange} />);

    const textarea = screen.getByPlaceholderText('SELECT * FROM sales WHERE DATE(date) = :date');
    fireEvent.change(textarea, { target: { value: 'SELECT 1' } });

    expect(onChange).toHaveBeenCalledWith('SELECT 1');
  });

  it('inserts two spaces on Tab key press without losing focus', () => {
    const onChange = vi.fn();
    render(<SqlEditor queryType="sql" queryContent="SELECT" onChange={onChange} />);

    const textarea = screen.getByPlaceholderText('SELECT * FROM sales WHERE DATE(date) = :date') as HTMLTextAreaElement;
    textarea.selectionStart = 6;
    textarea.selectionEnd = 6;

    fireEvent.keyDown(textarea, { key: 'Tab' });

    expect(onChange).toHaveBeenCalledWith('SELECT  ');
  });

  it('formats SQL when clicking format button', () => {
    const onChange = vi.fn();
    const rawSql = 'select id, name from users where status = 1 and age > 18';

    render(<SqlEditor queryType="sql" queryContent={rawSql} onChange={onChange} />);

    const formatBtn = screen.getByTitle('格式化 SQL');
    fireEvent.click(formatBtn);

    expect(onChange).toHaveBeenCalled();
    const formatted = onChange.mock.calls[0][0];
    expect(formatted).toContain('SELECT');
    expect(formatted).toContain('FROM');
    expect(formatted).toContain('WHERE');
  });

  it('clears content when clicking clear button', () => {
    const onChange = vi.fn();
    render(<SqlEditor queryType="sql" queryContent="SELECT 1" onChange={onChange} />);

    const clearBtn = screen.getByTitle('清空内容');
    fireEvent.click(clearBtn);

    expect(onChange).toHaveBeenCalledWith('');
  });

  it('formats complex SQL queries with multiple clauses and preserved literals', () => {
    const raw = "select id, name, 'hello world' as msg from orders where date = :date and type = 'partner' group by id order by id desc limit 10";
    const formatted = formatSql(raw);

    expect(formatted).toContain('SELECT');
    expect(formatted).toContain('\nFROM orders');
    expect(formatted).toContain('\nWHERE date = :date');
    expect(formatted).toContain("\n  AND type = 'partner'");
    expect(formatted).toContain('\nGROUP BY id');
    expect(formatted).toContain('\nORDER BY id');
    expect(formatted).toContain('\nLIMIT 10');
    // Ensure string literal is fully intact
    expect(formatted).toContain("'hello world'");
    expect(formatted).toContain("'partner'");
  });

  it('safely handles comments without commenting out subsequent clauses', () => {
    const raw = 'select id -- check user\n, name from users';
    const formatted = formatSql(raw);
    expect(formatted).toContain('-- check user');
    // Ensure -- comment is followed by a newline so , name isn't swallowed
    const commentIndex = formatted.indexOf('-- check user');
    const newlineAfterComment = formatted.indexOf('\n', commentIndex);
    expect(newlineAfterComment).toBeGreaterThan(commentIndex);
    expect(formatted.indexOf(', name')).toBeGreaterThan(newlineAfterComment);
  });
});
