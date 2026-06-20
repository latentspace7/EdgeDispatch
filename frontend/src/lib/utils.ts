import { type ClassValue, clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function generateId(): string {
  return crypto.randomUUID()
}

export function formatTimestamp(ts: number): string {
  const date = new Date(ts * 1000)
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffMins = Math.floor(diffMs / 60000)
  const diffHours = Math.floor(diffMs / 3600000)
  const diffDays = Math.floor(diffMs / 86400000)

  if (diffMins < 1) return 'Just now'
  if (diffMins < 60) return `${diffMins}m ago`
  if (diffHours < 24) return `${diffHours}h ago`
  if (diffDays < 7) return `${diffDays}d ago`
  return date.toLocaleDateString()
}

export function truncate(str: string, maxLen: number): string {
  if (str.length <= maxLen) return str
  return str.slice(0, maxLen).trimEnd() + '…'
}
