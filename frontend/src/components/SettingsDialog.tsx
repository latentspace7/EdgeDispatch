import { useState, useEffect } from 'react'
import { X, SlidersHorizontal, Cpu, Cloud, DollarSign, Save, FileText } from 'lucide-react'
import type { Settings } from '@/lib/types'
import { fetchSettings, updateSettings, type PricingUpdate } from '@/lib/api'

interface Props {
  open: boolean
  onClose: () => void
  threshold: number
  showHandoffDetails: boolean
  onThresholdChange: (t: number) => void
  onPricingChange?: (inputPerMTok: number, outputPerMTok: number) => void
  onShowHandoffDetailsChange: (show: boolean) => void
}

export default function SettingsDialog({
  open,
  onClose,
  threshold,
  showHandoffDetails,
  onThresholdChange,
  onPricingChange,
  onShowHandoffDetailsChange,
}: Props) {
  const [settings, setSettings] = useState<Settings | null>(null)
  const [localThreshold, setLocalThreshold] = useState(threshold)
  const [priceInput, setPriceInput] = useState<number>(5)
  const [priceOutput, setPriceOutput] = useState<number>(15)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (open) {
      setError(null)
      setSaving(false)
      setLocalThreshold(threshold)
      fetchSettings()
        .then((s) => {
          setSettings(s)
          setLocalThreshold(s.toolThreshold)
          setPriceInput(s.priceInputPerMTok)
          setPriceOutput(s.priceOutputPerMTok)
          onThresholdChange(s.toolThreshold)
          onPricingChange?.(s.priceInputPerMTok, s.priceOutputPerMTok)
        })
        .catch((e) => {
          console.error('Failed to load settings:', e)
          setError('Could not load settings. Check that the backend is running.')
        })
    }
  }, [open, threshold, onThresholdChange, onPricingChange])

  const handleSave = async () => {
    if (saving) return

    setError(null)
    setSaving(true)
    try {
      const update: PricingUpdate = {
        threshold: localThreshold,
        priceInputPerMTok: priceInput,
        priceOutputPerMTok: priceOutput,
      }
      const updated = await updateSettings(update)
      setLocalThreshold(updated.toolThreshold)
      setPriceInput(updated.priceInputPerMTok)
      setPriceOutput(updated.priceOutputPerMTok)
      onThresholdChange(updated.toolThreshold)
      onPricingChange?.(updated.priceInputPerMTok, updated.priceOutputPerMTok)
      setSettings(updated)
      onClose()
    } catch (e) {
      console.error('Failed to save settings:', e)
      setError('Settings were not saved. Check the backend connection and try again.')
    } finally {
      setSaving(false)
    }
  }

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-[#242424]/35 backdrop-blur-sm" onClick={onClose} />

      {/* Dialog */}
      <div className="relative w-full max-w-md mx-4 glass rounded-xl border border-[#242424]/14 shadow-neon-violet overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-[#d4d1c8] bg-white">
          <div className="flex items-center gap-2">
            <SlidersHorizontal className="w-4 h-4 text-[#e2231a]" />
            <h2 className="text-sm font-semibold text-[#242424]">Settings</h2>
          </div>
          <button
            onClick={onClose}
            className="w-8 h-8 rounded-lg flex items-center justify-center text-[#6d6a62] hover:text-[#242424] hover:bg-[#f3f2ec] transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Body */}
        <div className="p-4 space-y-6 bg-[#f8f7f3]">
          {/* Threshold slider */}
          <div>
            <label className="text-xs text-[#424242] uppercase tracking-wider font-medium mb-2 block">
              Tool Threshold
            </label>
            <div className="flex items-center gap-3">
              <input
                type="range"
                min={1}
                max={10}
                value={localThreshold}
                onChange={(e) => setLocalThreshold(Number(e.target.value))}
                className="flex-1 h-1.5 rounded-full appearance-none bg-[#d4d1c8] cursor-pointer
                  [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-4
                  [&::-webkit-slider-thumb]:h-4 [&::-webkit-slider-thumb]:rounded-full
                  [&::-webkit-slider-thumb]:bg-[#e2231a] [&::-webkit-slider-thumb]:shadow-neon-cyan
                  [&::-webkit-slider-thumb]:cursor-pointer"
              />
              <span className="text-sm font-mono text-[#242424] min-w-[2ch] text-center">
                {localThreshold}
              </span>
            </div>
            <p className="text-[11px] text-[#6d6a62] mt-1.5">
              Max MCP tool calls before escalating to cloud model
            </p>
          </div>

          {/* Pricing */}
          <div>
            <label className="text-xs text-[#424242] uppercase tracking-wider font-medium mb-2 flex items-center gap-1.5">
              <DollarSign className="w-3 h-3 text-[#e2231a]" />
              Cloud Pricing (per 1M tokens)
            </label>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <p className="text-[11px] text-[#6d6a62] mb-1">Input $/M</p>
                <input
                  type="number"
                  min={0}
                  step={0.01}
                  value={priceInput}
                  onChange={(e) => setPriceInput(Number(e.target.value))}
                  className="w-full px-3 py-2 rounded-lg bg-white border border-[#d4d1c8] text-sm text-[#242424] font-mono focus:outline-none focus:border-[#e2231a]/70"
                />
              </div>
              <div>
                <p className="text-[11px] text-[#6d6a62] mb-1">Output $/M</p>
                <input
                  type="number"
                  min={0}
                  step={0.01}
                  value={priceOutput}
                  onChange={(e) => setPriceOutput(Number(e.target.value))}
                  className="w-full px-3 py-2 rounded-lg bg-white border border-[#d4d1c8] text-sm text-[#242424] font-mono focus:outline-none focus:border-[#e2231a]/70"
                />
              </div>
            </div>
            <p className="text-[11px] text-[#6d6a62] mt-1.5">
              Used to compute per-query cost savings vs a monolithic baseline.
              Update when API pricing changes.
            </p>
          </div>

          {/* Debug visibility */}
          <div>
            <label className="text-xs text-[#424242] uppercase tracking-wider font-medium mb-2 flex items-center gap-1.5">
              <FileText className="w-3 h-3 text-[#e2231a]" />
              Handoff Visibility
            </label>
            <label className="flex items-center justify-between gap-3 p-3 rounded-lg bg-white border border-[#d4d1c8] cursor-pointer">
              <div className="min-w-0">
                <p className="text-sm text-[#242424]">Show handoff details</p>
                <p className="text-[11px] text-[#6d6a62]">
                  Escalated replies
                </p>
              </div>
              <input
                type="checkbox"
                checked={showHandoffDetails}
                onChange={(e) => onShowHandoffDetailsChange(e.target.checked)}
                className="h-4 w-4 rounded border-[#d4d1c8] bg-white text-[#e2231a] accent-[#e2231a] focus:ring-[#e2231a]/30"
              />
            </label>
          </div>

          {/* Model info */}
          {settings && (
            <div className="space-y-3">
              <div className="flex items-center gap-3 p-3 rounded-lg bg-white border border-[#d4d1c8]">
                <Cpu className="w-4 h-4 text-[#e2231a] flex-shrink-0" />
                <div className="min-w-0">
                  <p className="text-xs text-[#6d6a62]">Local Model</p>
                  <p className="text-sm text-[#242424] truncate font-mono">{settings.localModel}</p>
                </div>
              </div>

              <div className="flex items-center gap-3 p-3 rounded-lg bg-white border border-[#d4d1c8]">
                <Cloud className="w-4 h-4 text-[#e2231a] flex-shrink-0" />
                <div className="min-w-0">
                  <p className="text-xs text-[#6d6a62]">Cloud Model</p>
                  <p className="text-sm text-[#242424] truncate font-mono">{settings.highEndModel}</p>
                </div>
              </div>

              <div className="flex items-center gap-3 p-3 rounded-lg bg-white border border-[#d4d1c8]">
                <DollarSign className="w-4 h-4 text-[#242424] flex-shrink-0" />
                <div className="min-w-0">
                  <p className="text-xs text-[#6d6a62]">MCP Servers</p>
                  <p className="text-sm text-[#242424] font-mono">{settings.mcpServerCount} connected</p>
                </div>
              </div>
            </div>
          )}

          {error && (
            <div
              role="alert"
              className="rounded-xl border border-edge-rose/30 bg-edge-rose/10 px-3 py-2 text-xs text-rose-900"
            >
              {error}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex justify-end gap-2 p-4 border-t border-[#d4d1c8] bg-white">
          <button
            onClick={onClose}
            className="px-4 py-2 text-xs text-[#6d6a62] hover:text-[#242424] transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={saving || !Number.isFinite(priceInput) || !Number.isFinite(priceOutput)}
            className="inline-flex items-center gap-1.5 px-4 py-2 text-xs bg-[#e2231a] text-white border border-[#b41414] rounded-lg hover:bg-[#b41414] transition-colors disabled:opacity-50"
          >
            <Save className="h-3.5 w-3.5" />
            {saving ? 'Saving...' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  )
}
