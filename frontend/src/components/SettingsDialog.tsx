import { useState, useEffect } from 'react'
import { X, SlidersHorizontal, Cpu, Cloud, DollarSign } from 'lucide-react'
import type { Settings } from '@/lib/types'
import { fetchSettings, updateSettings, type PricingUpdate } from '@/lib/api'

interface Props {
  open: boolean
  onClose: () => void
  threshold: number
  onThresholdChange: (t: number) => void
  onPricingChange?: (inputPerMTok: number, outputPerMTok: number) => void
}

export default function SettingsDialog({
  open,
  onClose,
  threshold,
  onThresholdChange,
  onPricingChange,
}: Props) {
  const [settings, setSettings] = useState<Settings | null>(null)
  const [localThreshold, setLocalThreshold] = useState(threshold)
  const [priceInput, setPriceInput] = useState<number>(5)
  const [priceOutput, setPriceOutput] = useState<number>(15)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (open) {
      fetchSettings()
        .then((s) => {
          setSettings(s)
          setPriceInput(s.priceInputPerMTok)
          setPriceOutput(s.priceOutputPerMTok)
        })
        .catch(console.error)
      setLocalThreshold(threshold)
    }
  }, [open, threshold])

  const handleSave = async () => {
    setSaving(true)
    try {
      const update: PricingUpdate = {
        threshold: localThreshold,
        priceInputPerMTok: priceInput,
        priceOutputPerMTok: priceOutput,
      }
      const updated = await updateSettings(update)
      onThresholdChange(localThreshold)
      onPricingChange?.(updated.priceInputPerMTok, updated.priceOutputPerMTok)
      setSettings(updated)
    } catch (e) {
      console.error('Failed to save settings:', e)
    }
    setSaving(false)
  }

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />

      {/* Dialog */}
      <div className="relative w-full max-w-md mx-4 glass rounded-2xl border border-cyber-border shadow-neon-violet overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-cyber-border">
          <div className="flex items-center gap-2">
            <SlidersHorizontal className="w-4 h-4 text-edge-violet" />
            <h2 className="text-sm font-semibold text-white">Settings</h2>
          </div>
          <button
            onClick={onClose}
            className="w-8 h-8 rounded-lg flex items-center justify-center text-slate-500 hover:text-white hover:bg-cyber-card transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Body */}
        <div className="p-4 space-y-6">
          {/* Threshold slider */}
          <div>
            <label className="text-xs text-slate-400 uppercase tracking-wider font-medium mb-2 block">
              Tool Threshold
            </label>
            <div className="flex items-center gap-3">
              <input
                type="range"
                min={1}
                max={10}
                value={localThreshold}
                onChange={(e) => setLocalThreshold(Number(e.target.value))}
                className="flex-1 h-1.5 rounded-full appearance-none bg-cyber-border cursor-pointer
                  [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-4
                  [&::-webkit-slider-thumb]:h-4 [&::-webkit-slider-thumb]:rounded-full
                  [&::-webkit-slider-thumb]:bg-edge-violet [&::-webkit-slider-thumb]:shadow-neon-violet
                  [&::-webkit-slider-thumb]:cursor-pointer"
              />
              <span className="text-sm font-mono text-edge-violet min-w-[2ch] text-center">
                {localThreshold}
              </span>
            </div>
            <p className="text-[11px] text-slate-500 mt-1.5">
              Max MCP tool calls before escalating to cloud model
            </p>
          </div>

          {/* Pricing */}
          <div>
            <label className="text-xs text-slate-400 uppercase tracking-wider font-medium mb-2 flex items-center gap-1.5">
              <DollarSign className="w-3 h-3 text-edge-emerald" />
              Cloud Pricing (per 1M tokens)
            </label>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <p className="text-[11px] text-slate-500 mb-1">Input $/M</p>
                <input
                  type="number"
                  min={0}
                  step={0.01}
                  value={priceInput}
                  onChange={(e) => setPriceInput(Number(e.target.value))}
                  className="w-full px-3 py-2 rounded-xl bg-cyber-card/50 border border-cyber-border text-sm text-slate-200 font-mono focus:outline-none focus:border-edge-emerald/40"
                />
              </div>
              <div>
                <p className="text-[11px] text-slate-500 mb-1">Output $/M</p>
                <input
                  type="number"
                  min={0}
                  step={0.01}
                  value={priceOutput}
                  onChange={(e) => setPriceOutput(Number(e.target.value))}
                  className="w-full px-3 py-2 rounded-xl bg-cyber-card/50 border border-cyber-border text-sm text-slate-200 font-mono focus:outline-none focus:border-edge-emerald/40"
                />
              </div>
            </div>
            <p className="text-[11px] text-slate-500 mt-1.5">
              Used to compute per-query cost savings vs a monolithic baseline.
              Update when API pricing changes.
            </p>
          </div>

          {/* Model info */}
          {settings && (
            <div className="space-y-3">
              <div className="flex items-center gap-3 p-3 rounded-xl bg-cyber-card/50 border border-cyber-border">
                <Cpu className="w-4 h-4 text-edge-cyan flex-shrink-0" />
                <div className="min-w-0">
                  <p className="text-xs text-slate-400">Local Model</p>
                  <p className="text-sm text-slate-200 truncate font-mono">{settings.localModel}</p>
                </div>
              </div>

              <div className="flex items-center gap-3 p-3 rounded-xl bg-cyber-card/50 border border-cyber-border">
                <Cloud className="w-4 h-4 text-edge-violet flex-shrink-0" />
                <div className="min-w-0">
                  <p className="text-xs text-slate-400">Cloud Model</p>
                  <p className="text-sm text-slate-200 truncate font-mono">{settings.highEndModel}</p>
                </div>
              </div>

              <div className="flex items-center gap-3 p-3 rounded-xl bg-cyber-card/50 border border-cyber-border">
                <DollarSign className="w-4 h-4 text-edge-emerald flex-shrink-0" />
                <div className="min-w-0">
                  <p className="text-xs text-slate-400">MCP Servers</p>
                  <p className="text-sm text-slate-200 font-mono">{settings.mcpServerCount} connected</p>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex justify-end gap-2 p-4 border-t border-cyber-border">
          <button
            onClick={onClose}
            className="px-4 py-2 text-xs text-slate-400 hover:text-white transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            className="px-4 py-2 text-xs bg-edge-violet/20 text-edge-violet border border-edge-violet/30 rounded-xl hover:bg-edge-violet/30 transition-colors disabled:opacity-50"
          >
            {saving ? 'Saving...' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  )
}
