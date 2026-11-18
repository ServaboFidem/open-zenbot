import React, { useEffect, useRef } from 'react';
import { dayTitle, moveKey } from './dates.js';

const MOVES = new Set(['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End']);
const WEEKDAYS = [['Mon', 'Monday'], ['Tue', 'Tuesday'], ['Wed', 'Wednesday'], ['Thu', 'Thursday'], ['Fri', 'Friday'], ['Sat', 'Saturday'], ['Sun', 'Sunday']];

const count = n => `${n} ${n === 1 ? 'event' : 'events'}`;

// The month as a grid of day buttons (Monday first). One button is in the tab order (the selected day,
// or the 1st); arrow keys, Home and End move the selection, and a move that leaves the grid turns the month.
// props: grid (monthGrid), year, month, selected, today (day keys), byDay (Map key -> events),
// colorOf(event) -> a CSS colour, onSelect(key, turnMonth)
export default function CalendarGrid({ grid, year, month, selected, today, byDay, colorOf, onSelect }) {
  const buttons = useRef(new Map());
  const focusTo = useRef(null);
  useEffect(() => {
    if (focusTo.current && buttons.current.has(focusTo.current)) { buttons.current.get(focusTo.current).focus(); focusTo.current = null; }
  });
  const keys = new Set(grid.flat());
  const prefix = `${String(year).padStart(4, '0')}-${String(month).padStart(2, '0')}-`;
  const inMonth = key => key.startsWith(prefix);
  const tabKey = keys.has(selected) ? selected : `${prefix}01`;

  function keyDown(e, key) {
    if (!MOVES.has(e.key) || e.altKey || e.ctrlKey || e.metaKey) return;
    e.preventDefault();
    const next = moveKey(key, e.key);
    focusTo.current = next;
    onSelect(next, !keys.has(next));
  }

  return <div role="grid" className="events-grid" aria-label={dayTitle(`${prefix}01`, { month: 'long', year: 'numeric' })}>
    <div role="row" className="events-week events-weekdays">
      {WEEKDAYS.map(([short, long]) => <div role="columnheader" key={short} aria-label={long}>{short}</div>)}
    </div>
    {grid.map(week => <div role="row" className="events-week" key={week[0]}>
      {week.map(key => {
        const list = byDay.get(key) || [];
        const classes = ['events-day', inMonth(key) ? '' : 'is-outside', key === selected ? 'is-selected' : '', key === today ? 'is-today' : ''].filter(Boolean).join(' ');
        return <div role="gridcell" key={key} aria-selected={key === selected}>
          <button type="button" className={classes} tabIndex={key === tabKey ? 0 : -1}
            ref={el => { if (el) buttons.current.set(key, el); else buttons.current.delete(key); }}
            aria-label={`${dayTitle(key)}, ${count(list.length)}${key === today ? ', today' : ''}`}
            aria-current={key === today ? 'date' : undefined}
            onClick={() => onSelect(key, !inMonth(key))} onKeyDown={e => keyDown(e, key)}>
            <span className="events-day-number">{Number(key.slice(8))}</span>
            {list.slice(0, 2).map(ev => <span className="events-day-item" key={ev.id}>
              <span className="events-swatch" style={{ background: colorOf(ev) }}/>
              <span className="events-day-title">{ev.title}</span>
            </span>)}
            {list.length > 2 && <span className="events-day-more">+{list.length - 2} more</span>}
            {list.length > 0 && <span className="events-day-count" aria-hidden="true">{list.length}</span>}
          </button>
        </div>;
      })}
    </div>)}
  </div>;
}
