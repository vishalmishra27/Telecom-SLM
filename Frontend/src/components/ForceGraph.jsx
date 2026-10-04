import { useEffect, useRef, useState, useCallback, useMemo } from 'react'
import * as d3 from 'd3'
import { LocateFixed, Minus, Plus, Maximize2 } from 'lucide-react'

/* ------------------------------------------------------------------ */
/*  Ontology colour palette                                           */
/* ------------------------------------------------------------------ */
const classColors = {
  Incident: '#e05d4f', Alarm: '#f0a13a', AlarmProbableCause: '#d9465f',
  KPIObservation: '#3e80c2', Service: '#0091DA', Subscriber: '#8b67bd',
  Runbook: '#3c8c55', Resolution: '#237b65', RootCauseAnalysis: '#17201e',
  FiberSpan: '#547486', PowerSystem: '#c68327', Generator: '#b27024',
  BatteryBackup: '#888f41', Cell: '#5f9bb7', CellSite: '#8aa056',
  gNodeB: '#486fb7', Router: '#6d7f8f', Tower: '#8a7962',
  Threshold: '#9a9fa6', Product: '#5f77c8', Complaint: '#b55a8a',
  ChangeRecord: '#6f6a90', System: '#58606a', CustomerFacingService: '#0091DA',
  Device: '#536d79', IPInterface: '#3f83a8', ProcessStep: '#8c6a3d',
  Employee: '#7a6ea8', Vendor: '#7d8150', DeviceModel: '#697b8f',
  DeviceFirmware: '#8a7972', CommonPolicy: '#4f7c68', Customer: '#3e80c2',
  BillingAccount: '#5f77c8', Invoice: '#c68327', ChargingRecord: '#b27024',
  Payment: '#42c49e', Dunning: '#e05d4f', Adjustment: '#888f41',
  LogEvent: '#58606a', PMCounter: '#547486', ServiceProblem: '#8b67bd',
  Entity: '#6d7b78',
}
const defaultColor = '#6d7b78'

const classAbbrev = {
  Customer: 'CUST', BillingAccount: 'ACCT', Invoice: 'INV', ChargingRecord: 'CHG',
  Payment: 'PAY', Dunning: 'DUN', Adjustment: 'ADJ', Alarm: 'ALM',
  KPIObservation: 'KPI', PMCounter: 'PM', ServiceProblem: 'SVC', Complaint: 'CMP',
  LogEvent: 'LOG', RootCauseAnalysis: 'RCA', Incident: 'INC', Resolution: 'RES',
  Runbook: 'RUN', CellSite: 'SITE', gNodeB: 'GNB', Cell: 'CELL', Router: 'RTR',
  Tower: 'TWR', FiberSpan: 'FBR', PowerSystem: 'PWR', Generator: 'GEN',
  BatteryBackup: 'BAT', Threshold: 'THR', ChangeRecord: 'CHR',
}

/* ------------------------------------------------------------------ */
/*  Helpers                                                           */
/* ------------------------------------------------------------------ */
function shortLabel(node) {
  const v = node.name || node.id
  return v.length > 18 ? `${v.slice(0, 16)}…` : v
}

function nodeRadius(cls) {
  if (cls === 'Customer' || cls === 'RootCauseAnalysis') return 24
  if (cls === 'BillingAccount') return 20
  return 15
}

function abbrevFor(cls) {
  return classAbbrev[cls] || (cls || 'ENT').slice(0, 3).toUpperCase()
}

function colorFor(node) {
  if (typeof node === 'string') return classColors[node] || defaultColor
  return node._color || classColors[node.ontology_class] || defaultColor
}

/** Stable identity key for a graph so we only restart the simulation when the graph changes */
function graphId(graph) {
  if (!graph?.nodes?.length) return ''
  const ids = graph.nodes.map((n) => n.id).sort().join(',')
  return `${ids}:${graph.relationships?.length || 0}`
}

/* ------------------------------------------------------------------ */
/*  Component                                                         */
/* ------------------------------------------------------------------ */
export default function ForceGraph({ graph, selectedId, focusedId, highlightIds, onSelect, onFocus }) {
  const containerRef = useRef(null)
  const svgRef = useRef(null)
  const simRef = useRef(null)       // d3.forceSimulation
  const zoomRef = useRef(null)      // d3.zoom behaviour
  const nodesRef = useRef([])       // live mutable node array (d3 mutates .x .y)
  const linksRef = useRef([])       // live mutable link array (d3 mutates .source .target → objects)
  const gLinkRef = useRef(null)     // <g> for links
  const gNodeRef = useRef(null)     // <g> for nodes
  const prevGraphId = useRef('')

  const [tooltip, setTooltip] = useState(null)
  const [hoveredId, setHoveredId] = useState(null)

  // Selected / related IDs (React state so tooltip + controls re-render)
  const selectedRef = useRef(selectedId)
  selectedRef.current = selectedId

  // Keep highlight ref in sync so graph rebuild can apply it
  const highlightRef = useRef(highlightIds)
  highlightRef.current = highlightIds

  const relatedIds = useMemo(() => {
    if (!selectedId) return new Set()
    const ids = new Set([selectedId])
    ;(graph?.relationships || []).forEach((r) => {
      const sid = typeof r.source === 'object' ? r.source.id : r.source
      const tid = typeof r.target === 'object' ? r.target.id : r.target
      if (sid === selectedId) ids.add(tid)
      if (tid === selectedId) ids.add(sid)
    })
    return ids
  }, [graph, selectedId])

  const relatedRef = useRef(relatedIds)
  relatedRef.current = relatedIds

  /* ---------------------------------------------------------------- */
  /*  Tick — called by d3 on every simulation tick                    */
  /* ---------------------------------------------------------------- */
  const tick = useCallback(() => {
    const linkSel = d3.select(gLinkRef.current).selectAll('.fg-link-g')
    const nodeSel = d3.select(gNodeRef.current).selectAll('.fg-node-g')

    // Update link positions — lines always attach to node centres
    linkSel.select('line')
      .attr('x1', (d) => d.source.x)
      .attr('y1', (d) => d.source.y)
      .attr('x2', (d) => d.target.x)
      .attr('y2', (d) => d.target.y)

    // Update link labels
    linkSel.select('text')
      .attr('x', (d) => (d.source.x + d.target.x) / 2)
      .attr('y', (d) => (d.source.y + d.target.y) / 2)

    // Update node positions
    nodeSel.attr('transform', (d) => `translate(${d.x},${d.y})`)
  }, [])

  /* ---------------------------------------------------------------- */
  /*  Build / rebuild the simulation + SVG when graph data changes    */
  /* ---------------------------------------------------------------- */
  useEffect(() => {
    const container = containerRef.current
    const svg = svgRef.current
    if (!container || !svg) return

    const gid = graphId(graph)
    if (!gid) {
      // Empty graph — clear SVG
      d3.select(gLinkRef.current).selectAll('*').remove()
      d3.select(gNodeRef.current).selectAll('*').remove()
      if (simRef.current) { simRef.current.stop(); simRef.current = null }
      prevGraphId.current = ''
      return
    }

    // Only rebuild when the actual graph data changes
    if (gid === prevGraphId.current) return
    prevGraphId.current = gid

    // Stop old simulation
    if (simRef.current) simRef.current.stop()

    // Build fresh nodes & links (shallow copies so d3 can mutate)
    const nodeMap = new Map()
    const nodes = graph.nodes.map((n) => {
      const copy = { ...n, x: undefined, y: undefined }
      nodeMap.set(n.id, copy)
      return copy
    })
    const nodeIds = new Set(nodes.map((n) => n.id))
    const links = (graph.relationships || [])
      .filter((r) => {
        const sid = typeof r.source === 'object' ? r.source.id : r.source
        const tid = typeof r.target === 'object' ? r.target.id : r.target
        return nodeIds.has(sid) && nodeIds.has(tid)
      })
      .map((r) => ({
        ...r,
        source: typeof r.source === 'object' ? r.source.id : r.source,
        target: typeof r.target === 'object' ? r.target.id : r.target,
      }))

    nodesRef.current = nodes
    linksRef.current = links

    // Get dimensions
    const { width, height } = container.getBoundingClientRect()
    const w = width || 900
    const h = height || 620

    // ----- d3 force simulation -----
    const sim = d3.forceSimulation(nodes)
      .force('link', d3.forceLink(links).id((d) => d.id).distance(110).strength(0.4))
      .force('charge', d3.forceManyBody().strength(-320).distanceMax(400))
      .force('center', d3.forceCenter(w / 2, h / 2).strength(0.05))
      .force('x', d3.forceX(w / 2).strength(0.03))
      .force('y', d3.forceY(h / 2).strength(0.03))
      .force('collision', d3.forceCollide().radius((d) => nodeRadius(d.ontology_class) + 14).strength(0.7))
      .alphaDecay(0.02)
      .velocityDecay(0.35)

    simRef.current = sim

    // ----- Build SVG via d3 (imperative) -----
    const gLink = d3.select(gLinkRef.current)
    const gNode = d3.select(gNodeRef.current)

    // Clear previous
    gLink.selectAll('*').remove()
    gNode.selectAll('*').remove()

    // --- Links ---
    // Ensure each link has a unique id for d3 key
    links.forEach((l, i) => { if (!l.id) l.id = `link-${i}` })

    const linkG = gLink.selectAll('.fg-link-g')
      .data(links, (d) => d.id)
      .join('g')
      .attr('class', 'fg-link-g')

    linkG.append('line')
      .attr('class', 'fg-link-line')
      .attr('marker-end', 'url(#fg-arrow)')

    linkG.append('text')
      .attr('class', 'fg-link-label')
      .attr('text-anchor', 'middle')
      .attr('dy', -5)
      .text((d) => d.type)

    // --- Nodes ---
    const nodeG = gNode.selectAll('.fg-node-g')
      .data(nodes, (d) => d.id)
      .join('g')
      .attr('class', 'fg-node-g')
      .style('cursor', 'pointer')

    // Outer halo (hidden by default, shown via CSS when selected)
    nodeG.append('circle')
      .attr('class', 'fg-halo')
      .attr('r', (d) => nodeRadius(d.ontology_class) + 8)

    // Main circle
    nodeG.append('circle')
      .attr('class', 'fg-circle')
      .attr('r', (d) => nodeRadius(d.ontology_class))
      .attr('fill', (d) => colorFor(d))

    // Inner highlight circle
    nodeG.append('circle')
      .attr('class', 'fg-inner')
      .attr('r', (d) => nodeRadius(d.ontology_class) * 0.5)
      .attr('fill', 'rgba(255,255,255,0.12)')
      .style('pointer-events', 'none')

    // Abbreviation text
    nodeG.append('text')
      .attr('class', 'fg-abbrev')
      .attr('text-anchor', 'middle')
      .attr('dominant-baseline', 'central')
      .attr('dy', 0.5)
      .text((d) => abbrevFor(d.ontology_class))

    // Label text
    nodeG.append('text')
      .attr('class', 'fg-label')
      .attr('text-anchor', 'middle')
      .attr('dy', (d) => nodeRadius(d.ontology_class) + 14)
      .text((d) => shortLabel(d))

    // --- Drag behaviour ---
    const drag = d3.drag()
      .on('start', (event, d) => {
        if (!event.active) sim.alphaTarget(0.15).restart()
        d.fx = d.x
        d.fy = d.y
      })
      .on('drag', (event, d) => {
        d.fx = event.x
        d.fy = event.y
      })
      .on('end', (event, d) => {
        if (!event.active) sim.alphaTarget(0)
        // Keep node pinned where user dropped it
        d.fx = d.x
        d.fy = d.y
      })

    nodeG.call(drag)

    // --- Click / double-click ---
    nodeG.on('click', (event, d) => {
      event.stopPropagation()
      onSelect(d)
    })
    nodeG.on('dblclick', (event, d) => {
      event.stopPropagation()
      // Unpin node on double-click
      d.fx = null
      d.fy = null
      sim.alphaTarget(0.1).restart()
      setTimeout(() => sim.alphaTarget(0), 600)
      onSelect(d)
      onFocus(d)
    })

    // --- Hover ---
    nodeG.on('mouseenter', (event, d) => {
      const bounds = container.getBoundingClientRect()
      setHoveredId(d.id)
      setTooltip({
        x: event.clientX - bounds.left + 14,
        y: event.clientY - bounds.top - 10,
        node: d,
      })
    })
    nodeG.on('mousemove', (event, d) => {
      const bounds = container.getBoundingClientRect()
      setTooltip({
        x: event.clientX - bounds.left + 14,
        y: event.clientY - bounds.top - 10,
        node: d,
      })
    })
    nodeG.on('mouseleave', () => {
      setHoveredId(null)
      setTooltip(null)
    })

    // --- Apply highlight if present (after rebuild) ---
    const hl = highlightRef.current
    if (hl && hl.size > 0) {
      nodeG.classed('is-highlight', (d) => hl.has(d.id))
        .classed('is-bg', (d) => !hl.has(d.id))
        .select('.fg-halo')
        .classed('is-glow', (d) => hl.has(d.id))
      linkG.classed('is-highlight-link', (d) => {
        const sid = typeof d.source === 'object' ? d.source.id : d.source
        const tid = typeof d.target === 'object' ? d.target.id : d.target
        return hl.has(sid) && hl.has(tid)
      }).classed('is-bg-link', (d) => {
        const sid = typeof d.source === 'object' ? d.source.id : d.source
        const tid = typeof d.target === 'object' ? d.target.id : d.target
        return !(hl.has(sid) && hl.has(tid))
      })
    }

    // --- Simulation tick ---
    sim.on('tick', tick)

    // --- Zoom ---
    if (!zoomRef.current) {
      const zoom = d3.zoom()
        .scaleExtent([0.15, 5])
        .on('zoom', (event) => {
          d3.select(gLinkRef.current).attr('transform', event.transform)
          d3.select(gNodeRef.current).attr('transform', event.transform)
        })
      zoomRef.current = zoom
      d3.select(svg).call(zoom).on('dblclick.zoom', null)
    }

    // Auto-fit after layout settles
    const fitTimer = setTimeout(() => {
      fitGraph()
    }, 800)

    return () => {
      clearTimeout(fitTimer)
    }
  }, [graph, tick, onSelect, onFocus])

  /* ---------------------------------------------------------------- */
  /*  Update visual classes when selection changes                    */
  /* ---------------------------------------------------------------- */
  useEffect(() => {
    if (!gNodeRef.current || !gLinkRef.current) return
    const sel = selectedId
    const related = relatedIds

    // Nodes
    d3.select(gNodeRef.current).selectAll('.fg-node-g')
      .classed('is-selected', (d) => d.id === sel)
      .classed('is-dimmed', (d) => sel && !related.has(d.id))
      .select('.fg-halo')
      .classed('is-visible', (d) => d.id === sel)

    // Links
    d3.select(gLinkRef.current).selectAll('.fg-link-g')
      .classed('is-related', (d) => {
        const sid = typeof d.source === 'object' ? d.source.id : d.source
        const tid = typeof d.target === 'object' ? d.target.id : d.target
        return sel && (sid === sel || tid === sel)
      })
  }, [selectedId, relatedIds])

  /* ---------------------------------------------------------------- */
  /*  Update highlight styling when highlightIds changes              */
  /* ---------------------------------------------------------------- */
  useEffect(() => {
    if (!gNodeRef.current || !gLinkRef.current) return
    const hasHighlight = highlightIds && highlightIds.size > 0

    // Nodes: add glow to highlighted, dim others
    d3.select(gNodeRef.current).selectAll('.fg-node-g')
      .classed('is-highlight', (d) => hasHighlight && highlightIds.has(d.id))
      .classed('is-bg', (d) => hasHighlight && !highlightIds.has(d.id))
      .select('.fg-halo')
      .classed('is-glow', (d) => hasHighlight && highlightIds.has(d.id))

    // Links: highlight if both endpoints are in the highlight set
    d3.select(gLinkRef.current).selectAll('.fg-link-g')
      .classed('is-highlight-link', (d) => {
        if (!hasHighlight) return false
        const sid = typeof d.source === 'object' ? d.source.id : d.source
        const tid = typeof d.target === 'object' ? d.target.id : d.target
        return highlightIds.has(sid) && highlightIds.has(tid)
      })
      .classed('is-bg-link', (d) => {
        if (!hasHighlight) return false
        const sid = typeof d.source === 'object' ? d.source.id : d.source
        const tid = typeof d.target === 'object' ? d.target.id : d.target
        return !(highlightIds.has(sid) && highlightIds.has(tid))
      })
  }, [highlightIds])

  /* ---------------------------------------------------------------- */
  /*  Zoom helpers                                                    */
  /* ---------------------------------------------------------------- */
  const fitGraph = useCallback(() => {
    const svg = svgRef.current
    const zoom = zoomRef.current
    const nodes = nodesRef.current
    if (!svg || !zoom || !nodes.length) return

    const { width, height } = containerRef.current.getBoundingClientRect()
    if (!width || !height) return

    const pad = 60
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity
    nodes.forEach((n) => {
      if (n.x < minX) minX = n.x
      if (n.x > maxX) maxX = n.x
      if (n.y < minY) minY = n.y
      if (n.y > maxY) maxY = n.y
    })
    const gw = Math.max(1, maxX - minX)
    const gh = Math.max(1, maxY - minY)
    const scale = Math.min(2, Math.max(0.3, Math.min((width - pad * 2) / gw, (height - pad * 2) / gh)))
    const cx = (minX + maxX) / 2
    const cy = (minY + maxY) / 2
    const t = d3.zoomIdentity.translate(width / 2 - cx * scale, height / 2 - cy * scale).scale(scale)
    d3.select(svg).transition().duration(400).call(zoom.transform, t)
  }, [])

  const zoomBy = useCallback((factor) => {
    const svg = svgRef.current
    const zoom = zoomRef.current
    if (!svg || !zoom) return
    const { width, height } = containerRef.current.getBoundingClientRect()
    d3.select(svg).transition().duration(200).call(
      zoom.scaleBy, factor, [width / 2, height / 2]
    )
  }, [])

  const resetZoom = useCallback(() => {
    const svg = svgRef.current
    const zoom = zoomRef.current
    if (!svg || !zoom) return
    d3.select(svg).transition().duration(300).call(zoom.transform, d3.zoomIdentity)
  }, [])

  /* ---------------------------------------------------------------- */
  /*  Render                                                          */
  /* ---------------------------------------------------------------- */
  return (
    <div className="graph-stage" ref={containerRef}>
      <div className="graph-controls" aria-label="Graph zoom controls">
        <button type="button" title="Zoom in" onClick={() => zoomBy(1.35)}><Plus size={16} /></button>
        <button type="button" title="Zoom out" onClick={() => zoomBy(0.74)}><Minus size={16} /></button>
        <button type="button" title="Fit to view" onClick={fitGraph}><LocateFixed size={16} /></button>
        <button type="button" title="Reset zoom" onClick={resetZoom}><Maximize2 size={14} /></button>
      </div>

      {!graph?.nodes?.length ? (
        <div className="graph-empty"><span className="loader" />Loading ontology graph</div>
      ) : (
        <svg ref={svgRef} width="100%" height="100%">
          <defs>
            <marker id="fg-arrow" viewBox="0 -5 10 10" refX="28" refY="0"
              markerWidth="6" markerHeight="6" orient="auto">
              <path d="M0,-4L8,0L0,4" fill="#9eaaa6" />
            </marker>
            <marker id="fg-arrow-active" viewBox="0 -5 10 10" refX="30" refY="0"
              markerWidth="7" markerHeight="7" orient="auto">
              <path d="M0,-4L8,0L0,4" fill="#00338D" />
            </marker>
          </defs>
          <g ref={gLinkRef} />
          <g ref={gNodeRef} />
        </svg>
      )}

      {tooltip && (
        <div className="graph-tooltip" style={{ left: tooltip.x, top: tooltip.y }}>
          <div className="graph-tooltip-class" style={{ color: colorFor(tooltip.node) }}>
            {tooltip.node.ontology_class}
          </div>
          <div className="graph-tooltip-id">{tooltip.node.id}</div>
          {tooltip.node.name && tooltip.node.name !== tooltip.node.id && (
            <div className="graph-tooltip-name">{tooltip.node.name}</div>
          )}
          <div className="graph-tooltip-hint">Click to select · Double-click to expand · Drag to move</div>
        </div>
      )}
    </div>
  )
}

export { classColors }
