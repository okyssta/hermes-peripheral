/**
 * Peripheral — desktop half.
 *
 * A "Screen memory" pane: search the OCR text the agent already indexes, browse
 * frames as thumbnails, open one to read it, and forget what should not be kept.
 * Everything comes from the plugin's own backend routes
 * (`/api/plugins/peripheral/*`); nothing here reads app state or the DOM.
 *
 * Plain ESM, loaded uncompiled: UI is jsx() calls, no JSX syntax.
 * Allowed imports only: @hermes/plugin-sdk, react, react/jsx-runtime.
 */

import { PALETTE_AREA, cn, haptic, host } from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'
import { useCallback, useEffect, useRef, useState } from 'react'

const ID = 'peripheral'
const PAGE = 24

const paneClass = 'flex h-full min-h-0 flex-col text-sm'
const rowClass =
  'flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left transition-colors ' +
  'hover:bg-(--chrome-action-hover)'
const muted = 'text-(--ui-text-tertiary)'
const faint = 'text-(--ui-text-quaternary)'
const smallBtn =
  'inline-flex items-center gap-1 rounded-md border border-(--ui-stroke-secondary) px-2 py-1 ' +
  'text-xs text-(--ui-text-secondary) transition-colors hover:bg-(--chrome-action-hover) ' +
  'disabled:opacity-40'

function Thumb({ src, alt }) {
  if (!src) {
    return jsx('div', {
      className:
        'h-9 w-14 shrink-0 rounded border border-(--ui-stroke-secondary) bg-(--ui-bg-secondary)',
      'aria-hidden': 'true'
    })
  }
  return jsx('img', {
    src,
    alt: alt || '',
    className: 'h-9 w-14 shrink-0 rounded border border-(--ui-stroke-secondary) object-cover'
  })
}

function Detail({ ctx, item, onForgotten }) {
  const [sure, setSure] = useState(false)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    setSure(false)
  }, [item && item.id])

  const forget = useCallback(async () => {
    if (!item) return
    setBusy(true)
    try {
      await ctx.rest('/forget', { method: 'POST', body: { id: item.id } })
      haptic('tap')
      onForgotten()
    } catch {
      setBusy(false)
    }
  }, [ctx, item, onForgotten])

  if (!item) {
    return jsx('div', {
      className: cn('flex h-full items-center justify-center px-6 text-center text-xs', faint),
      children: 'Pick a screen to read it.'
    })
  }

  const facts = [
    item.when,
    item.width && item.height ? item.width + 'x' + item.height : '',
    item.chars ? item.chars + ' chars' : 'no text'
  ]
    .filter(Boolean)
    .join(' · ')

  return jsxs('div', {
    className: 'flex min-h-0 flex-col gap-2 border-t border-(--ui-stroke-secondary) p-3',
    children: [
      jsx('div', {
        className: 'truncate font-medium',
        children: item.title || 'Screen #' + item.id
      }),
      jsx('div', { className: cn('text-xs', muted), children: facts }),
      item.image
        ? jsx('img', {
            src: item.image,
            alt: '',
            className:
              'max-h-56 w-full rounded border border-(--ui-stroke-secondary) object-contain'
          })
        : null,
      jsx('pre', {
        className:
          'min-h-0 flex-1 overflow-auto whitespace-pre-wrap break-words rounded border ' +
          'border-(--ui-stroke-secondary) p-2 text-xs text-(--ui-text-secondary)',
        children: item.text || 'No text was read from this screen.'
      }),
      jsxs('div', {
        className: 'flex items-center gap-2',
        children: [
          jsx('button', {
            type: 'button',
            className: smallBtn,
            disabled: busy,
            onClick: () => (sure ? forget() : setSure(true)),
            children: busy ? 'Forgetting…' : sure ? 'Forget — are you sure?' : 'Forget'
          }),
          sure
            ? jsx('button', {
                type: 'button',
                className: smallBtn,
                onClick: () => setSure(false),
                children: 'Keep'
              })
            : null
        ]
      })
    ]
  })
}

function Pane({ ctx }) {
  const [items, setItems] = useState([])
  const [total, setTotal] = useState(0)
  const [q, setQ] = useState('')
  const [detail, setDetail] = useState(null)
  const [selected, setSelected] = useState(null)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')
  const [status, setStatus] = useState(null)
  const mounted = useRef(true)

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])

  const load = useCallback(
    async (query) => {
      setBusy(true)
      try {
        const suffix = query ? '&q=' + encodeURIComponent(query) : ''
        const res = await ctx.rest('/shots?limit=' + PAGE + suffix)
        if (!mounted.current) return
        setItems((res && res.items) || [])
        setTotal((res && res.total) || 0)
        setNote('')
      } catch {
        if (mounted.current) setNote('Backend unavailable — is the plugin enabled for this profile?')
      } finally {
        if (mounted.current) setBusy(false)
      }
    },
    [ctx]
  )

  const loadStatus = useCallback(async () => {
    try {
      const res = await ctx.rest('/status')
      if (mounted.current) setStatus(res)
    } catch {
      /* the pane still works through /shots */
    }
  }, [ctx])

  useEffect(() => {
    load('')
    loadStatus()
  }, [load, loadStatus])

  const open = useCallback(
    async (id) => {
      setSelected(id)
      try {
        const res = await ctx.rest('/shot/' + id + '?image=true')
        if (mounted.current) setDetail(res)
      } catch {
        if (mounted.current) setDetail(null)
      }
    },
    [ctx]
  )

  const act = useCallback(
    async (path, body) => {
      setBusy(true)
      try {
        const res = await ctx.rest(path, { method: 'POST', body: body || {} })
        if (res && res.line) setNote(res.line)
        else if (res && typeof res.deleted === 'number') setNote('forgot ' + res.deleted + ' screen(s)')
        await load(q)
        await loadStatus()
        return res
      } catch {
        setNote('That did not work.')
        return null
      } finally {
        if (mounted.current) setBusy(false)
      }
    },
    [ctx, load, loadStatus, q]
  )

  const onSearch = useCallback(
    (event) => {
      if (event && event.preventDefault) event.preventDefault()
      load(q)
    },
    [load, q]
  )

  const empty = !busy && items.length === 0

  return jsxs('div', {
    className: paneClass,
    children: [
      jsxs('div', {
        className: 'flex items-center gap-2 border-b border-(--ui-stroke-secondary) p-2',
        children: [
          jsx('form', {
            className: 'min-w-0 flex-1',
            onSubmit: onSearch,
            children: jsx('input', {
              type: 'search',
              value: q,
              placeholder: 'Words that were on screen…',
              onChange: (event) => setQ(event.target.value),
              className:
                'w-full rounded-md border border-(--ui-stroke-secondary) bg-transparent px-2 py-1 ' +
                'text-xs text-(--ui-text-secondary) outline-none focus:border-(--ui-accent)'
            })
          }),
          jsx('button', {
            type: 'button',
            className: smallBtn,
            disabled: busy,
            onClick: () => {
              setQ('')
              load('')
            },
            children: 'Clear'
          })
        ]
      }),
      jsxs('div', {
        className: cn('flex items-center justify-between px-3 py-1 text-[0.6875rem]', faint),
        children: [
          jsx('span', {
            children:
              (status ? status.count : total) +
              ' screen' +
              ((status ? status.count : total) === 1 ? '' : 's') +
              (q ? ' · filtered' : '')
          }),
          jsx('span', {
            children: status
              ? status.ocr
                ? 'ocr ready'
                : 'ocr missing'
              : ''
          })
        ]
      }),
      jsx('div', {
        className: 'min-h-0 flex-1 overflow-y-auto px-2 pb-2',
        children: empty
          ? jsx('div', {
              className: cn('px-4 py-8 text-center text-xs leading-relaxed', faint),
              children: note || 'Nothing indexed yet. Capture the screen, or let the watcher do it.'
            })
          : items.map((item) =>
              jsxs(
                'button',
                {
                  type: 'button',
                  className: cn(
                    rowClass,
                    selected === item.id ? 'bg-(--chrome-action-hover)' : ''
                  ),
                  onClick: () => open(item.id),
                  children: [
                    jsx(Thumb, { src: item.thumb, alt: item.title }),
                    jsxs('span', {
                      className: 'min-w-0 flex-1',
                      children: [
                        jsx('span', {
                          className: 'block truncate text-xs text-(--ui-text-secondary)',
                          children: item.title || 'Screen #' + item.id
                        }),
                        jsxs('span', {
                          className: cn('block truncate text-[0.6875rem]', faint),
                          children: [
                            item.ago,
                            item.snippet ? ' · ' + item.snippet : ''
                          ]
                        })
                      ]
                    })
                  ]
                },
                item.id
              )
            )
      }),
      note
        ? jsx('div', {
            className: cn('border-t border-(--ui-stroke-secondary) px-3 py-1 text-xs', muted),
            children: note
          })
        : null,
      jsxs('div', {
        className: 'flex items-center gap-2 border-t border-(--ui-stroke-secondary) p-2',
        children: [
          jsx('button', {
            type: 'button',
            className: smallBtn,
            disabled: busy,
            onClick: () => act('/capture'),
            children: 'Capture now'
          }),
          jsx('button', {
            type: 'button',
            className: smallBtn,
            disabled: busy,
            onClick: () => act('/scan'),
            children: 'Index screenshots'
          }),
          jsx('button', {
            type: 'button',
            className: cn(smallBtn, 'ml-auto'),
            disabled: busy,
            onClick: () => load(q),
            children: busy ? 'Working…' : 'Refresh'
          })
        ]
      }),
      jsx(Detail, {
        ctx,
        item: detail,
        onForgotten: () => {
          setDetail(null)
          setSelected(null)
          load(q)
          loadStatus()
        }
      })
    ]
  })
}

function Chip({ ctx }) {
  const [count, setCount] = useState(null)

  useEffect(() => {
    let alive = true
    const read = async () => {
      try {
        const res = await ctx.rest('/status')
        if (alive && res) setCount(res.count)
      } catch {
        /* keep the last number */
      }
    }
    read()
    const timer = setInterval(read, 60_000)
    return () => {
      alive = false
      clearInterval(timer)
    }
  }, [ctx])

  return jsx('button', {
    type: 'button',
    className: cn(
      'inline-flex h-full items-center gap-1 px-1.5 text-[0.6875rem] transition-colors',
      'text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover)'
    ),
    onClick: () => {
      haptic('tap')
      host.notify({
        kind: 'info',
        message:
          count === null
            ? 'Peripheral: index not reachable'
            : 'Peripheral: ' + count + ' screen' + (count === 1 ? '' : 's') + ' indexed'
      })
    },
    children: count === null ? 'screen —' : 'screen ' + count
  })
}

export default {
  id: ID,
  name: 'Peripheral',
  defaultEnabled: false,
  register(ctx) {
    ctx.register({
      id: 'memory',
      area: 'panes',
      title: 'Screen memory',
      data: { placement: 'right', width: '430px' },
      render: () => jsx(Pane, { ctx })
    })

    ctx.register({
      id: 'chip',
      area: 'statusBar.right',
      order: 131,
      render: () => jsx(Chip, { ctx })
    })

    ctx.register({
      id: 'capture',
      area: PALETTE_AREA,
      data: {
        id: 'peripheral.capture',
        label: 'Peripheral: remember this screen',
        keywords: ['screenshot', 'screen', 'capture', 'peripheral'],
        run: async () => {
          try {
            const res = await ctx.rest('/capture', { method: 'POST', body: {} })
            host.notify({
              kind: res && res.ok ? 'info' : 'error',
              message: (res && res.line) || 'Peripheral could not capture the screen'
            })
          } catch {
            host.notify({ kind: 'error', message: 'Peripheral backend is not reachable' })
          }
        }
      }
    })
  }
}
