export async function loadSummary() {
  const res = await fetch('/data/ontology/summary.json')
  if (!res.ok) throw new Error(`Failed to load summary: ${res.statusText}`)
  return res.json()
}

export async function loadTopLevel() {
  const res = await fetch('/data/ontology/top-level.json')
  if (!res.ok) throw new Error(`Failed to load top-level: ${res.statusText}`)
  return res.json()
}

export async function loadClasses() {
  const res = await fetch('/data/ontology/classes.json')
  if (!res.ok) throw new Error(`Failed to load classes: ${res.statusText}`)
  return res.json()
}

export async function loadFieldsForDomain(domain) {
  const filename = domain.toLowerCase()
  const res = await fetch(`/data/ontology/fields/${filename}.json`)
  if (!res.ok) throw new Error(`Failed to load fields for ${domain}: ${res.statusText}`)
  return res.json()
}

export async function loadRelationshipTypes() {
  const res = await fetch('/data/ontology/relationship-types.json')
  if (!res.ok) throw new Error(`Failed to load relationship types: ${res.statusText}`)
  return res.json()
}

export async function loadRelationshipMapping() {
  const res = await fetch('/data/ontology/relationship-mapping.json')
  if (!res.ok) throw new Error(`Failed to load relationship mapping: ${res.statusText}`)
  return res.json()
}

export async function loadUseCases() {
  const res = await fetch('/data/ontology/use-cases.json')
  if (!res.ok) throw new Error(`Failed to load use cases: ${res.statusText}`)
  return res.json()
}

export async function loadKpiCatalog() {
  const res = await fetch('/data/ontology/kpi-catalog.json')
  if (!res.ok) throw new Error(`Failed to load KPI catalog: ${res.statusText}`)
  return res.json()
}

export async function loadAlarmCatalog() {
  const res = await fetch('/data/ontology/alarm-catalog.json')
  if (!res.ok) throw new Error(`Failed to load alarm catalog: ${res.statusText}`)
  return res.json()
}

export async function loadOwlRules() {
  const res = await fetch('/data/ontology/owl-rules.json')
  if (!res.ok) throw new Error(`Failed to load OWL rules: ${res.statusText}`)
  return res.json()
}

export async function loadOwlDatatypes() {
  const res = await fetch('/data/ontology/owl-datatypes.json')
  if (!res.ok) throw new Error(`Failed to load OWL datatypes: ${res.statusText}`)
  return res.json()
}
