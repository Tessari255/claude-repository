import type { SVGProps } from 'react'

const CAMINHOS: Record<string, JSX.Element> = {
  play: <polygon points="7 4 20 12 7 20 7 4" />,
  flag: <><path d="M5 21V4" /><path d="M5 4h12l-2.5 4L17 12H5" /></>,
  value: <><rect x="3.5" y="6" width="17" height="12" rx="2.5" /><path d="M8 12h8" /></>,
  calc: <><rect x="5" y="3" width="14" height="18" rx="2.5" /><path d="M8 7h8M8 12h2M14 12h2M8 16h2M14 16h2" /></>,
  text: <path d="M5 7V4.5h14V7M12 4.5v15M9 19.5h6" />,
  pick: <><path d="M4 4h6v6H4zM14 14h6v6h-6z" /><path d="M10 7h3a4 4 0 0 1 4 4v3" /></>,
  branch: <><circle cx="6" cy="5.5" r="2" /><circle cx="6" cy="18.5" r="2" /><circle cx="18" cy="9" r="2" /><path d="M6 7.5v9M18 11c0 4-6 3-12 6" /></>,
  loop: <><path d="M17 2.5l3.5 3.5-3.5 3.5" /><path d="M3.5 11V9.5A3.5 3.5 0 0 1 7 6h13.5" /><path d="M7 21.5L3.5 18 7 14.5" /><path d="M20.5 13v1.5A3.5 3.5 0 0 1 17 18H3.5" /></>,
  python: <path d="M9 7l-5 5 5 5M15 7l5 5-5 5M13.5 5l-3 14" />,
  plus: <path d="M12 5v14M5 12h14" />,
  trash: <path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3" />,
  copy: <><rect x="9" y="9" width="11" height="11" rx="2" /><path d="M5 15V6a2 2 0 0 1 2-2h9" /></>,
  save: <><path d="M5 3h11l3 3v15H5z" /><path d="M8 3v6h8V3M8 21v-7h8v7" /></>,
  download: <path d="M12 4v11M7 11l5 5 5-5M5 20h14" />,
  upload: <path d="M12 16V5M7 9l5-5 5 5M5 20h14" />,
  search: <><circle cx="11" cy="11" r="6.5" /><path d="M20 20l-4.2-4.2" /></>,
  x: <path d="M6 6l12 12M18 6L6 18" />,
  check: <path d="M5 12.5l4.5 4.5L19 7" />,
  alert: <><path d="M12 3.8l9.2 16.2H2.8z" /><path d="M12 10v4.5M12 17.4v.4" /></>,
  info: <><circle cx="12" cy="12" r="9" /><path d="M12 11v6M12 7.4v.4" /></>,
  skip: <path d="M5 5l9 7-9 7zM18 5v14" />,
  clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
  chevronDown: <path d="M6 9l6 6 6-6" />,
  chevronRight: <path d="M9 6l6 6-6 6" />,
  edit: <path d="M4 20h4L19 9l-4-4L4 16z" />,
  zoomIn: <><circle cx="11" cy="11" r="6.5" /><path d="M20 20l-4.2-4.2M11 8v6M8 11h6" /></>,
  zoomOut: <><circle cx="11" cy="11" r="6.5" /><path d="M20 20l-4.2-4.2M8 11h6" /></>,
  fit: <path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" />,
  help: <><circle cx="12" cy="12" r="9" /><path d="M9.6 9.4a2.5 2.5 0 1 1 3.6 2.2c-.8.5-1.2 1-1.2 1.9M12 17.2v.3" /></>,
  spinner: <path d="M12 3a9 9 0 1 0 9 9" />,
  home: <path d="M4 11l8-7 8 7M6 10v10h12V10" />,
  lock: <><rect x="5" y="11" width="14" height="9" rx="2" /><path d="M8 11V8a4 4 0 0 1 8 0v3" /></>,
  keyboard: <><rect x="3" y="6" width="18" height="12" rx="2" /><path d="M7 10h.01M11 10h.01M15 10h.01M7 14h10" /></>,
  panelLeft: <><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M9 4v16" /></>,
  panelRight: <><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M15 4v16" /></>,
  eraser: <path d="M8 19l-4-4 9.5-9.5 6 6L12 19zM12 19h8" />,
  history: <><path d="M4 12a8 8 0 1 0 2.5-5.8L4 8.5" /><path d="M4 4v4.5h4.5M12 8v4l3 2" /></>,
}

export type NomeIcone = keyof typeof CAMINHOS | (string & {})

export function Icon({ name, size = 18, ...rest }: { name: NomeIcone; size?: number } & SVGProps<SVGSVGElement>) {
  return (
    <svg
      width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false" {...rest}
    >
      {CAMINHOS[name] ?? CAMINHOS.value}
    </svg>
  )
}

/** Marca da Trama: três fios (terracota, anil e açafrão) tecidos por dois fios de tinta. */
export function Logo({ size = 32 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true" focusable="false">
      <rect width="32" height="32" rx="7" fill="#1E2430" />
      <rect x="5" y="7" width="22" height="4.5" rx="2.25" fill="#E0694B" />
      <rect x="5" y="13.75" width="22" height="4.5" rx="2.25" fill="#7CA3E6" />
      <rect x="5" y="20.5" width="22" height="4.5" rx="2.25" fill="#F2B84B" />
      <rect x="10" y="4" width="4.5" height="24" rx="2.25" fill="#F7F2E8" opacity=".92" />
      <rect x="17.5" y="4" width="4.5" height="24" rx="2.25" fill="#F7F2E8" opacity=".92" />
      <rect x="10" y="12.5" width="4.5" height="7" fill="#7CA3E6" />
      <rect x="17.5" y="5.75" width="4.5" height="7" fill="#E0694B" />
      <rect x="17.5" y="19.25" width="4.5" height="7" fill="#F2B84B" />
    </svg>
  )
}
