"""What the demo organization's researchers work on: eight fields, each with MeSH headings,
the phrases its titles and abstracts are made of, its journals, and its clinical studies.

Everything here is invented except the MeSH heading names, which are the public vocabulary
real papers are indexed with. Journals have plausible but invented names.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Area:
    key: str
    division: str
    mesh: tuple[str, ...]  # the field's headings; the first few are often major topics
    subjects: tuple[str, ...]  # what is studied (genes, cells, exposures)
    processes: tuple[str, ...]  # what it does (or the outcome)
    models: tuple[str, ...]  # where (systems, tissues, populations)
    methods: tuple[str, ...]  # how ("Single-cell multiomics")
    findings: tuple[str, ...]  # what was found
    background: tuple[str, ...]  # an abstract's opening sentence
    journals: tuple[str, ...]
    ic: str  # NIH institute code for the field's grants
    titles: tuple[str, ...] = ()  # templates; the shared ones when empty
    keywords: tuple[str, ...] = ()
    conditions: tuple[str, ...] = field(default=())  # for clinical studies


TITLES = (
    "{S} sustains {P} in {M}",
    "{T} reveals {F}",
    "Loss of {S} impairs {P}",
    "A role for {S} in {P}",
    "{S} shapes {P} in {M}",
    "{T} of {P} in {M}",
    "Distinct programs of {P} in {M}",
    "{F} in {M}",
    "Targeting {S} to restore {P}",
    "Temporal dynamics of {P} in {M}",
    "{S} links {P} to {F}",
    "Rewiring of {P} by {S}",
)

EPI_TITLES = (
    "{S} and {P} in {M}",
    "Association of {S} with {P} among {M}",
    "{T} of {S} to improve {P}",
    "{P} in {M}: {t}",
    "Trends in {P} among {M}",
    "{S} as a modifiable factor in {P}",
)

ABSTRACT = (
    "{B}",
    "Here, we used {t} in {M} to ask how {S} affects {P}.",
    "We found that {S} {verb} {P}, and that {F} {consequence}.",
    "{conclusion}",
)

EPI_ABSTRACT = (
    "{B}",
    "We examined {S} and {P} among {M}, using {t}.",
    "{S_cap} was associated with {direction} {P}, an association that persisted after adjustment for age, sex, and socioeconomic factors.",
    "{conclusion_epi}",
)

VERBS = ("is required for", "restrains", "tunes", "accelerates", "stabilizes", "licenses")
CONSEQUENCES = (
    "depends on it",
    "emerge early and persist",
    "are lost when it is deleted",
    "are conserved in human samples",
    "predict outcome",
)
CONCLUSIONS = (
    "These findings identify a pathway that could be targeted therapeutically.",
    "Together, our results suggest a model in which local signals set the pace of the response.",
    "This work provides a resource for studying these programs across tissues and disease states.",
    "Our data reconcile earlier observations and point to a shared regulatory mechanism.",
    "These results define a checkpoint that may be exploited to improve treatment.",
)
EPI_CONCLUSIONS = (
    "These findings support targeted outreach to groups at highest risk.",
    "Interventions addressing this factor could reduce the burden of disease at the population level.",
    "Further work should test whether these associations are causal.",
)
DIRECTIONS = ("lower", "higher", "reduced", "increased")

AREAS: tuple[Area, ...] = (
    Area(
        key="immunology",
        division="Division of Immunology and Infection",
        mesh=(
            "CD8-Positive T-Lymphocytes", "Immunologic Memory", "Lymphocyte Activation",
            "Germinal Center", "B-Lymphocytes", "Mucosal Immunity", "Receptors, Antigen, T-Cell",
            "Interleukin-7", "Antigen Presentation", "Cell Differentiation", "Cytokines",
            "T-Lymphocytes, Regulatory",
        ),
        subjects=(
            "TCF1", "the transcription factor BATF", "IL-7 receptor signaling", "CD103",
            "tissue-resident memory T cells", "regulatory T cells", "IL-21",
            "dendritic cell cross-presentation", "follicular helper T cells",
        ),
        processes=(
            "memory T cell persistence", "germinal center selection", "effector differentiation",
            "tissue residency", "antibody affinity maturation", "mucosal barrier defense",
            "recall responses",
        ),
        models=(
            "chronic viral infection", "the lung mucosa", "human lymph nodes", "aged mice",
            "the skin", "the intestinal epithelium",
        ),
        methods=(
            "Single-cell multiomics", "Lineage tracing", "CRISPR screening in primary T cells",
            "Spatial transcriptomics", "Longitudinal immune profiling",
        ),
        findings=(
            "a stem-like reservoir of memory T cells", "metabolic checkpoints in effector fate",
            "a niche that sustains resident memory", "clonal dynamics of germinal center B cells",
        ),
        background=(
            "Durable immunity depends on memory lymphocytes that persist for years after infection.",
            "Barrier tissues host specialized immune cells that respond within hours of reinfection.",
            "How lymphocytes balance effector function with long-term survival remains unclear.",
        ),
        journals=("Annals of Host Defense", "Immune Systems Letters", "Journal of Translational Immunity"),
        ic="AI",
        keywords=("immunological memory", "T cells", "germinal centers"),
    ),
    Area(
        key="infection",
        division="Division of Immunology and Infection",
        mesh=(
            "HIV-1", "HIV Infections", "Antibodies, Neutralizing", "Vaccines",
            "Influenza, Human", "Virus Replication", "Host-Pathogen Interactions",
            "Drug Resistance, Viral", "Viral Load", "Antiviral Agents",
            "Cytomegalovirus Infections", "Viral Envelope Proteins",
        ),
        subjects=(
            "broadly neutralizing antibodies", "the viral envelope glycoprotein", "SAMHD1",
            "interferon-stimulated genes", "the latent reservoir", "germline-targeting immunogens",
            "the capsid protein",
        ),
        processes=(
            "viral rebound after treatment interruption", "neutralization breadth",
            "viral replication", "antiviral restriction", "reservoir decay",
            "vaccine-elicited protection",
        ),
        models=(
            "people living with HIV", "nonhuman primates", "humanized mice",
            "a phase 1 vaccine trial", "transplant recipients", "primary airway cultures",
        ),
        methods=(
            "Deep mutational scanning", "Longitudinal sequencing", "Antibody repertoire sequencing",
            "Quantitative viral outgrowth assays", "Systems serology",
        ),
        findings=(
            "escape pathways from neutralizing antibodies", "determinants of reservoir size",
            "correlates of vaccine protection", "a conserved site of vulnerability",
        ),
        background=(
            "Antiviral therapy suppresses replication but does not eliminate infected cells.",
            "An effective vaccine must elicit antibodies that neutralize diverse viral strains.",
            "Viruses evolve rapidly under immune and drug pressure.",
        ),
        journals=("Journal of Viral Pathogenesis", "Vaccine Science Reports", "Infection and Host Response"),
        ic="AI",
        keywords=("HIV", "vaccines", "neutralizing antibodies"),
        conditions=("HIV Infections", "Cytomegalovirus Infections", "Influenza, Human"),
    ),
    Area(
        key="genomics",
        division="Division of Molecular Oncology",
        mesh=(
            "Leukemia, Myeloid, Acute", "Clonal Hematopoiesis", "Genomics", "DNA Repair",
            "Tumor Suppressor Proteins", "Gene Expression Regulation, Neoplastic",
            "Exome Sequencing", "Chromosomal Instability", "Breast Neoplasms",
            "Precision Medicine", "Biomarkers, Tumor", "Neoplasm Metastasis",
        ),
        subjects=(
            "TP53", "DNMT3A", "homologous recombination", "BRCA2", "the cohesin complex",
            "extrachromosomal DNA", "clonal hematopoiesis",
        ),
        processes=(
            "clonal evolution", "therapy resistance", "genome instability", "leukemic transformation",
            "metastatic seeding", "relapse after transplant",
        ),
        models=(
            "acute myeloid leukemia", "breast cancer", "patient-derived xenografts",
            "hematopoietic stem cells", "a population-based cohort",
        ),
        methods=(
            "Whole-genome sequencing", "Single-cell DNA sequencing", "Base-editing screens",
            "Long-read sequencing", "Cell-free DNA profiling",
        ),
        findings=(
            "the order of mutations in leukemia", "early drivers of relapse",
            "a signature of defective DNA repair", "selection for resistant subclones",
        ),
        background=(
            "Cancers evolve through the stepwise acquisition of somatic mutations.",
            "Blood cells accumulate mutations with age, and some clones expand.",
            "Resistance to therapy often arises from subclones present at diagnosis.",
        ),
        journals=("Cancer Genome Letters", "Journal of Hematologic Malignancy", "Annals of Tumor Evolution"),
        ic="CA",
        keywords=("cancer genomics", "leukemia", "clonal evolution"),
        conditions=("Leukemia, Myeloid, Acute", "Breast Neoplasms"),
    ),
    Area(
        key="immunotherapy",
        division="Division of Molecular Oncology",
        mesh=(
            "Immunotherapy, Adoptive", "Receptors, Chimeric Antigen", "Tumor Microenvironment",
            "Immune Checkpoint Inhibitors", "CD8-Positive T-Lymphocytes", "T-Lymphocytes, Cytotoxic",
            "Multiple Myeloma", "Lymphoma, Large B-Cell, Diffuse", "Melanoma",
            "Cytokine Release Syndrome", "Tumor Escape", "Antigens, Neoplasm",
        ),
        subjects=(
            "CAR T cells", "BCMA-directed therapy", "PD-1 blockade", "tumor-infiltrating lymphocytes",
            "engineered T cell receptors", "the tumor stroma", "IL-15 support",
        ),
        processes=(
            "CAR T cell persistence", "antitumor immunity", "T cell exhaustion",
            "antigen escape", "response to checkpoint blockade", "durable remission",
        ),
        models=(
            "multiple myeloma", "large B-cell lymphoma", "metastatic melanoma",
            "solid tumors", "a first-in-human trial",
        ),
        methods=(
            "Clonal tracking", "Spatial proteomics", "Pooled CRISPR knockout screens",
            "Single-cell TCR sequencing", "Serial biopsies",
        ),
        findings=(
            "determinants of durable response", "mechanisms of antigen loss",
            "a stem-like CAR T cell state", "stromal barriers to T cell infiltration",
        ),
        background=(
            "Engineered T cells can induce remission in blood cancers, but many patients relapse.",
            "Checkpoint blockade benefits a minority of patients with solid tumors.",
            "The tumor microenvironment limits the function of infiltrating T cells.",
        ),
        journals=("Journal of Cellular Immunotherapy", "Tumor Immunology Reports", "Annals of Tumor Evolution"),
        ic="CA",
        keywords=("CAR T cells", "immunotherapy", "tumor microenvironment"),
        conditions=("Multiple Myeloma", "Lymphoma, Large B-Cell, Diffuse", "Melanoma"),
    ),
    Area(
        key="neuroscience",
        division="Division of Neuroscience",
        mesh=(
            "Neurons", "Synapses", "Hippocampus", "Memory", "Neuronal Plasticity", "Microglia",
            "Astrocytes", "Alzheimer Disease", "Sleep", "Neural Pathways", "Brain",
            "Neurodegenerative Diseases",
        ),
        subjects=(
            "microglial pruning", "dendritic spines", "the locus coeruleus", "astrocytic calcium",
            "tau", "sleep spindles", "hippocampal place cells",
        ),
        processes=(
            "memory consolidation", "synaptic plasticity", "neuroinflammation",
            "circuit remodeling", "neurodegeneration", "learning",
        ),
        models=(
            "the aging brain", "mouse models of Alzheimer disease", "human cortical organoids",
            "the developing cortex", "freely behaving mice",
        ),
        methods=(
            "Two-photon imaging", "Spatial transcriptomics", "Optogenetic manipulation",
            "High-density electrophysiology", "Connectomic reconstruction",
        ),
        findings=(
            "a glial checkpoint for synapse loss", "replay sequences that predict recall",
            "early vulnerability of noradrenergic neurons", "state-dependent plasticity rules",
        ),
        background=(
            "Memories are stabilized after learning through changes at synapses.",
            "Glial cells actively shape neural circuits in health and disease.",
            "Neurodegeneration begins years before symptoms appear.",
        ),
        journals=("Neural Circuits Review", "Journal of Brain Plasticity", "Neurodegeneration Letters"),
        ic="NS",
        keywords=("memory", "microglia", "synaptic plasticity"),
    ),
    Area(
        key="structural",
        division="Division of Structural and Computational Biology",
        mesh=(
            "Cryoelectron Microscopy", "Protein Conformation", "Membrane Proteins",
            "Ion Channels", "Receptors, G-Protein-Coupled", "Protein Folding",
            "Allosteric Regulation", "Molecular Dynamics Simulation", "Protein Binding",
            "Crystallography, X-Ray", "Viral Envelope Proteins", "Protein Multimerization",
        ),
        subjects=(
            "a mechanosensitive channel", "the fusion glycoprotein", "a class B GPCR",
            "the nuclear pore complex", "an ABC transporter", "chaperone networks",
        ),
        processes=(
            "membrane fusion", "ligand recognition", "allosteric activation", "gating",
            "substrate transport", "complex assembly",
        ),
        models=(
            "native membranes", "lipid nanodiscs", "intact cells", "reconstituted systems",
        ),
        methods=(
            "Cryo-electron microscopy", "Cryo-electron tomography", "Hydrogen-deuterium exchange",
            "Time-resolved crystallography", "Integrative modeling",
        ),
        findings=(
            "the structural basis of gating", "an intermediate state on the fusion pathway",
            "a cryptic allosteric pocket", "how lipids regulate assembly",
        ),
        background=(
            "Membrane proteins change shape to move ions, nutrients, and signals across membranes.",
            "Viral glycoproteins refold dramatically to fuse viral and host membranes.",
            "Large molecular machines assemble from dozens of subunits.",
        ),
        journals=("Structure and Mechanism", "Journal of Molecular Architecture", "Membrane Biology Reports"),
        ic="GM",
        keywords=("cryo-EM", "membrane proteins", "allostery"),
    ),
    Area(
        key="computational",
        division="Division of Structural and Computational Biology",
        mesh=(
            "Computational Biology", "Machine Learning", "Gene Regulatory Networks",
            "Single-Cell Analysis", "Transcriptome", "Algorithms", "Chromatin", "Epigenomics",
            "Sequence Analysis, RNA", "Deep Learning", "Gene Expression Profiling", "Software",
        ),
        subjects=(
            "chromatin accessibility", "enhancer grammar", "cell state transitions",
            "protein language models", "regulatory variants", "batch effects",
        ),
        processes=(
            "cell fate decisions", "gene regulation", "variant interpretation",
            "trajectory inference", "data integration",
        ),
        models=(
            "single-cell atlases", "developing tissues", "tumor samples",
            "population-scale cohorts", "hematopoiesis",
        ),
        methods=(
            "Deep generative modeling", "Graph neural networks", "Probabilistic modeling",
            "Transfer learning", "Multimodal integration",
        ),
        findings=(
            "regulatory programs that predict cell fate", "a scalable method for atlas integration",
            "sequence determinants of enhancer activity", "variants that alter splicing",
        ),
        background=(
            "Single-cell technologies measure the state of millions of cells, but integrating them is hard.",
            "Most disease-associated variants fall outside protein-coding genes.",
            "Machine learning models can learn regulatory rules from sequence.",
        ),
        journals=("Computational Genomics", "Journal of Biological Data Science", "Annals of Systems Biology"),
        ic="GM",
        keywords=("machine learning", "single-cell genomics", "gene regulation"),
    ),
    Area(
        key="population",
        division="Division of Population Health",
        mesh=(
            "Colorectal Neoplasms", "Early Detection of Cancer", "Mass Screening", "Risk Factors",
            "Diet", "Exercise", "Obesity", "Health Equity", "Socioeconomic Factors",
            "Cancer Survivors", "Smoking", "Health Services Accessibility",
        ),
        subjects=(
            "physical activity", "a Mediterranean-style diet", "neighborhood deprivation",
            "smoking cessation", "screening outreach by text message", "patient navigation",
            "metformin use",
        ),
        processes=(
            "colorectal cancer incidence", "survival after breast cancer",
            "uptake of colorectal cancer screening", "weight gain in early adulthood",
            "time to diagnosis", "cardiometabolic risk",
        ),
        models=(
            "a multiethnic cohort", "rural adults", "cancer survivors", "postmenopausal women",
            "a community health network", "adolescents and young adults",
        ),
        methods=(
            "A pragmatic randomized trial", "A prospective cohort study", "Mendelian randomization",
            "A cluster-randomized trial", "An interrupted time-series analysis",
        ),
        findings=(
            "persistent disparities in screening", "the long-term benefits of outreach",
            "modifiable contributors to risk",
        ),
        background=(
            "Colorectal cancer incidence is rising among adults younger than 50.",
            "Screening reduces cancer deaths, but uptake remains uneven across communities.",
            "Lifestyle factors account for a substantial share of cancer risk.",
        ),
        journals=("Journal of Population Cancer Research", "Preventive Health Reports", "Epidemiology and Outcomes"),
        ic="CA",
        titles=EPI_TITLES,
        keywords=("cancer screening", "health equity", "cohort study"),
        conditions=("Colorectal Neoplasms", "Obesity"),
    ),
)  # fmt: skip

# Fields that work together: (a, b) pairs whose labs coauthor now and then.
BRIDGES = (
    ("immunology", "immunotherapy"),
    ("infection", "structural"),
    ("computational", "genomics"),
    ("computational", "neuroscience"),
    ("population", "genomics"),
    ("immunology", "infection"),
)

# Clinical studies, hand-written so they read like real registry entries.
STUDIES: tuple[dict, ...] = (
    {"area": "immunotherapy", "title": "BCMA-Directed CAR T Cells With IL-15 Support in Relapsed Multiple Myeloma",
     "phases": ["PHASE1", "PHASE2"], "conditions": ["Multiple Myeloma"], "interventions": ["Immunotherapy, Adoptive"],
     "status": "RECRUITING", "enrollment": 64, "types": ["BIOLOGICAL"]},
    {"area": "immunotherapy", "title": "Dual-Antigen CAR T Cells for Large B-Cell Lymphoma After Prior CD19 Therapy",
     "phases": ["PHASE1"], "conditions": ["Lymphoma, Large B-Cell, Diffuse"], "interventions": ["Immunotherapy, Adoptive"],
     "status": "ACTIVE_NOT_RECRUITING", "enrollment": 32, "types": ["BIOLOGICAL"],
     "sponsor": "Halvane Therapeutics", "sponsor_class": "INDUSTRY"},
    {"area": "immunotherapy", "title": "Neoadjuvant PD-1 Blockade With Tumor-Infiltrating Lymphocytes in Melanoma",
     "phases": ["PHASE2"], "conditions": ["Melanoma"], "interventions": ["Immune Checkpoint Inhibitors"],
     "status": "COMPLETED", "enrollment": 48, "types": ["DRUG"],
     "sponsor": "National Cancer Institute (NCI)", "sponsor_class": "NIH"},
    {"area": "genomics", "title": "Measurable Residual Disease Monitoring by Sequencing After Transplant in Acute Myeloid Leukemia",
     "phases": [], "conditions": ["Leukemia, Myeloid, Acute"], "interventions": ["Precision Medicine"],
     "status": "RECRUITING", "enrollment": 220, "types": ["GENETIC"], "study_type": "OBSERVATIONAL"},
    {"area": "genomics", "title": "Cell-Free DNA Surveillance for Recurrence in Early Breast Cancer",
     "phases": ["NA"], "conditions": ["Breast Neoplasms"], "interventions": ["Biomarkers, Tumor"],
     "status": "RECRUITING", "enrollment": 400, "types": ["DIAGNOSTIC_TEST"]},
    {"area": "infection", "title": "Germline-Targeting Immunogen Priming Followed by Boosting in Healthy Adults",
     "phases": ["PHASE1"], "conditions": ["HIV Infections"], "interventions": ["Vaccines"],
     "status": "COMPLETED", "enrollment": 56, "types": ["BIOLOGICAL"],
     "sponsor": "Corvane Vaccine Trials Network", "sponsor_class": "NETWORK"},
    {"area": "infection", "title": "Broadly Neutralizing Antibodies During Analytic Treatment Interruption",
     "phases": ["PHASE2"], "conditions": ["HIV Infections"], "interventions": ["Antibodies, Neutralizing"],
     "status": "ACTIVE_NOT_RECRUITING", "enrollment": 40, "types": ["BIOLOGICAL"],
     "sponsor": "National Institute of Allergy and Infectious Diseases (NIAID)", "sponsor_class": "NIH"},
    {"area": "infection", "title": "Preemptive Antiviral Therapy for Cytomegalovirus After Stem Cell Transplant",
     "phases": ["PHASE3"], "conditions": ["Cytomegalovirus Infections"], "interventions": ["Antiviral Agents"],
     "status": "RECRUITING", "enrollment": 310, "types": ["DRUG"],
     "sponsor": "Meridane Pharmaceuticals", "sponsor_class": "INDUSTRY"},
    {"area": "population", "title": "Text Message Outreach to Increase Colorectal Cancer Screening in Community Clinics",
     "phases": ["NA"], "conditions": ["Colorectal Neoplasms"], "interventions": ["Mass Screening"],
     "status": "COMPLETED", "enrollment": 5200, "types": ["BEHAVIORAL"]},
    {"area": "population", "title": "A Walking Program to Reduce Weight Gain in Breast Cancer Survivors",
     "phases": ["NA"], "conditions": ["Obesity"], "interventions": ["Exercise"],
     "status": "RECRUITING", "enrollment": 260, "types": ["BEHAVIORAL"]},
)  # fmt: skip

GIVEN = (
    "Amara", "Ines", "Tomasz", "Priya", "Mateo", "Hana", "Kofi", "Lena", "Rafael", "Yuki",
    "Noor", "Elias", "Chiara", "Darius", "Mei", "Oskar", "Farah", "Julian", "Sana", "Ezra",
    "Ingrid", "Tariq", "Camila", "Henrik", "Aisha", "Luca", "Marisol", "Bjorn", "Nadia",
    "Arjun", "Saoirse", "Kenji", "Leila", "Andres", "Thandiwe", "Felix", "Rosa", "Dmitri",
    "Imani", "Paulo", "Greta", "Hamza", "Valentina", "Soren", "Yasmin", "Cyrus", "Ottilie",
    "Rohan", "Beatriz", "Kasimir", "Astrid", "Tobias", "Zara", "Nikhil", "Esme", "Kwame",
    "Liesel", "Matias", "Wren", "Anouk", "Idris", "Clara", "Teodor", "Sakura", "Emeka",
    "Freya", "Ignacio", "Talia", "Viktor", "Adaeze", "Jonah", "Mireille", "Ravi", "Selin",
    "Callum", "Dalia", "Haruto", "Lucia", "Omari", "Petra", "Quentin", "Rania", "Stellan",
    "Uma", "Wei", "Xiomara", "Yusuf", "Zofia", "Aurelio", "Brigid",
)  # fmt: skip
FAMILY = (
    "Okonkwo", "Lindqvist", "Varga", "Raghunathan", "Ferreira", "Takeda", "Mensah", "Nemeth",
    "Castillo", "Haddad", "Johansson", "Bianchi", "Kowalczyk", "Nakamura", "Adeyemi",
    "Petrov", "Moreau", "Sandoval", "Iyer", "Fitzgerald", "Holloway", "Achterberg",
    "Delacroix", "Mbeki", "Szabo", "Valdivia", "Ostrowski", "Kimura", "Brennan", "Oyelaran",
    "Lindgren", "Marchetti", "Rasmussen", "Quiroga", "Thorsen", "Abadi", "Koval", "Duchamp",
    "Elsayed", "Fontaine", "Gallardo", "Hartmann", "Ishikawa", "Jovanovic", "Kaur",
    "Lombardi", "Mwangi", "Nordin", "Olabode", "Pacheco", "Rautio", "Sorensen", "Tavares",
    "Ueda", "Vasquez", "Whitcombe", "Yilmaz", "Zielinski", "Amundsen", "Barros", "Chaudhry",
    "Draskovic", "Esposito", "Fagerlund", "Grabowski", "Hendricks", "Ivanova", "Jaramillo",
    "Kristiansen", "Laine", "Makinen", "Nwosu", "Oduya", "Pellegrini", "Quist", "Ribeiro",
    "Sato", "Tanaka", "Uzoma", "Vilhjalmsson", "Wexler", "Xavier", "Yamada", "Zamora",
    "Achebe", "Bergstrom", "Coelho", "Dahlberg", "Eriksen", "Falk", "Gunnarsdottir",
    "Halvorsen", "Iturbe", "Jankowski", "Kalnins", "Lacroix", "Mazur", "Nakashima",
    "Okafor", "Paredes", "Ranta", "Salonen", "Thibodeaux", "Urquhart", "Vance", "Wojcik",
    "Yeboah", "Zelenko", "Arnaud", "Bauer",
)  # fmt: skip
# Outside collaborators draw on their own surnames, so they never share one with an insider.
EXTERNAL_FAMILY = (
    "Abernathy", "Bellweather", "Cardoso", "Demir", "Ekwueme", "Fairbanks", "Gallo",
    "Hollis", "Ingersoll", "Jessup", "Kessler", "Lorenzo", "Mahlangu", "Nyberg", "Ortega",
    "Palmquist", "Radu", "Shackleton", "Toivonen", "Underhill", "Vestergaard", "Waller",
    "Yoshida", "Zuniga", "Anand", "Bricker", "Cheung", "Dorsey", "Engel", "Forsythe",
    "Goswami", "Hale", "Ito", "Juarez", "Kearney", "Ludwig", "Montoya", "Neville", "Odum",
    "Prieto",
)  # fmt: skip
EXTERNAL_PLACES = (
    "Department of Medicine, Ardmoor University, Ardmoor, UK.",
    "Institute of Molecular Medicine, University of Vellmar, Vellmar, Germany.",
    "School of Public Health, Kestrel Bay University, Kestrel Bay, Canada.",
    "Center for Infectious Disease Research, Halvard Medical College, Halvard, Norway.",
    "Department of Bioengineering, Solano Institute of Technology, Solano, USA.",
    "Faculty of Medicine, University of Montclair Ridge, Montclair Ridge, Australia.",
)
# Fields far from the organization's, for namesakes elsewhere.
ELSEWHERE = (
    ("Arabidopsis", "Plant Roots", "Photosynthesis", "Plant Leaves"),
    ("Nanoparticles", "Polymers", "Hydrogels", "Surface Properties"),
    ("Coral Reefs", "Ecosystem", "Climate Change", "Biodiversity"),
)
ELSEWHERE_JOURNALS = ("Plant Systems Biology", "Materials Interfaces", "Marine Ecology Letters")

# Clinical articles that cite the organization's papers (grant-to-impact threads). Journals are
# invented, and identifiers are drawn from a range no real PubMed record can have.
CLINICAL_TOPICS = {
    "Leukemia, Myeloid, Acute": "acute myeloid leukemia",
    "Breast Neoplasms": "breast cancer",
    "Multiple Myeloma": "multiple myeloma",
    "Lymphoma, Large B-Cell, Diffuse": "diffuse large B-cell lymphoma",
    "Melanoma": "melanoma",
    "HIV Infections": "HIV infection",
    "Cytomegalovirus Infections": "cytomegalovirus infection",
    "Influenza, Human": "influenza",
    "Colorectal Neoplasms": "colorectal cancer",
    "Obesity": "obesity",
}
# For areas whose studies name no condition, what their clinical citers write about.
CLINICAL_FALLBACK = {
    "immunology": ("autoimmune disease", "primary immunodeficiency"),
    "neuroscience": ("Parkinson disease", "chronic pain"),
    "structural": ("antimicrobial resistance", "cystic fibrosis"),
    "computational": ("cancer screening", "sepsis"),
}
CLINICAL_TITLES = {
    "guideline": (
        "Management of {c}: clinical practice guideline",
        "Consensus recommendations for the diagnosis and treatment of {c}",
        "Guideline update on the care of adults with {c}",
    ),
    "trial": (
        "A randomized trial of early combination therapy in {c}",
        "Phase 2 study of a targeted regimen for {c}",
        "Adapted dosing versus standard care in {c}: a randomized trial",
    ),
    "meta_analysis": (
        "Outcomes in {c}: a systematic review and meta-analysis",
        "Biomarkers of treatment response in {c}: a meta-analysis",
    ),
    "review": (
        "Advances in the clinical management of {c}",
        "From mechanism to clinic: emerging approaches to {c}",
    ),
    "clinical": (
        "Real-world outcomes of patients with {c}",
        "Clinical features and outcomes of {c} in a multicenter cohort",
    ),
}
CLINICAL_TYPES = {
    "guideline": ("Practice Guideline", "Journal Article"),
    "trial": ("Clinical Trial, Phase II", "Randomized Controlled Trial", "Journal Article"),
    "meta_analysis": ("Meta-Analysis", "Systematic Review", "Journal Article"),
    "review": ("Review", "Journal Article"),
    "clinical": ("Multicenter Study", "Journal Article"),
}
CLINICAL_WEIGHTS = {"guideline": 8, "trial": 30, "meta_analysis": 15, "review": 20, "clinical": 27}
CLINICAL_JOURNALS = (
    "Northbridge Medical Review",
    "Practice Guidance Bulletin",
    "Ward and Clinic Reports",
    "Corbel Journal of Clinical Evidence",
    "Trials in Medicine Today",
)

# Funding opportunities (wave 5). Federal ones are written as Grants.gov records with IDs and
# notice numbers no real opportunity has; "opens" and "closes" are days from the demo's today.
NIH_TYPES = ("06", "20", "12", "13", "22", "23", "00", "01", "02", "07", "11", "25")
OPPORTUNITIES = (
    {"number": "RFA-CA-27-991", "agency": "National Institutes of Health", "code": "HHS-NIH11",
     "title": "Next-Generation Cellular Immunotherapies for Hematologic Malignancies "
              "(R01 Clinical Trial Optional)",
     "text": "This notice invites research on chimeric antigen receptor (CAR) T cells for multiple "
             "myeloma and diffuse large B-cell lymphoma: antigen escape, cytokine support such as "
             "interleukin-15, T cell persistence and exhaustion, and early-phase studies that "
             "test them.", "opens": -30, "closes": 45, "ceiling": 750_000, "floor": 250_000},
    {"number": "RFA-AI-27-992", "agency": "National Institutes of Health", "code": "HHS-NIH11",
     "title": "Germline-Targeting Strategies for HIV Vaccine Development "
              "(U01 Clinical Trial Not Allowed)",
     "text": "Applications should develop germline-targeting immunogens and boosting regimens "
             "that elicit broadly neutralizing antibodies against HIV-1, including studies of B "
             "cell responses in germinal centers.", "opens": -20, "closes": 80,
     "ceiling": 1_200_000},
    {"number": "PAR-26-993", "agency": "National Institutes of Health", "code": "HHS-NIH11",
     "title": "Single-Cell Atlases of Clonal Hematopoiesis and Leukemia Evolution "
              "(R21 Clinical Trial Not Allowed)",
     "text": "Supports single-cell multiomics of clonal hematopoiesis and acute myeloid leukemia: "
             "how somatic mutations shape stem cell fitness, measurable residual disease, and "
             "relapse.", "opens": -60, "closes": 120, "ceiling": 275_000},
    {"number": "PAR-26-994", "agency": "National Institutes of Health", "code": "HHS-NIH11",
     "title": "Circuit Mechanisms of Chronic Pain (R01 Clinical Trial Not Allowed)",
     "text": "Seeks studies of the neural circuits and synaptic plasticity that sustain chronic "
             "pain, in animal models and human tissue, including neuroimmune interactions.",
     "opens": -90, "closes": 25, "ceiling": 500_000},
    {"number": "26-599", "agency": "National Science Foundation", "code": "NSF",
     "title": "Molecular Foundations of Membrane Fusion",
     "text": "Supports structural and biophysical research on the protein machinery of membrane "
             "fusion, including cryo-electron microscopy of viral fusion proteins.",
     "opens": -40, "closes": 65, "ceiling": 900_000, "types": ("06", "20", "12")},
    {"number": "RFA-CA-27-995", "agency": "National Institutes of Health", "code": "HHS-NIH11",
     "title": "Community Interventions to Increase Colorectal Cancer Screening "
              "(R01 Clinical Trial Required)",
     "text": "Invites trials of outreach to raise colorectal cancer screening in communities "
             "with low uptake, such as text-message reminders and mailed tests, with attention to "
             "obesity and other risk factors.", "opens": -15, "closes": 60,
     "ceiling": 650_000},
    {"number": "RFA-CA-27-996", "agency": "National Institutes of Health", "code": "HHS-NIH11",
     "title": "Machine Learning Methods for Integrating Multi-omic Cancer Data "
              "(U01 Clinical Trial Not Allowed)",
     "text": "Supports statistical and machine learning methods that integrate genomic, "
             "transcriptomic, and imaging data to predict cancer outcomes, with open software.",
     "opens": -10, "closes": 100, "ceiling": 800_000},
    {"number": "PAR-26-997", "agency": "National Institutes of Health", "code": "HHS-NIH11",
     "title": "Tissue-Resident Memory T Cells in Barrier Immunity "
              "(R01 Clinical Trial Not Allowed)",
     "text": "A forecast of a notice on tissue-resident memory T cells at mucosal barriers and "
             "how they protect against reinfection.", "forecast": True, "opens": 40,
     "closes": 150, "ceiling": 500_000},
    {"number": "PA-26-998", "agency": "National Institutes of Health", "code": "HHS-NIH11",
     "title": "Small Business Innovation in Cell Therapy Manufacturing "
              "(R43/R44 Clinical Trial Not Allowed)",
     "text": "Small businesses may propose technologies that make CAR T cell manufacturing "
             "faster, cheaper, and more consistent.", "opens": -45, "closes": 55,
     "ceiling": 300_000, "types": ("23",)},
    {"number": "PA-26-999", "agency": "National Institutes of Health", "code": "HHS-NIH11",
     "title": "Pathway to Independence Award in Cancer Immunology "
              "(K99/R00 Independent Clinical Trial Not Allowed)",
     "text": "Helps postdoctoral researchers in cancer immunology move to independent positions. "
             "Eligibility depends on the candidate's years of postdoctoral experience.",
     "opens": -50, "closes": 70, "ceiling": 250_000},
    {"number": "RFA-AI-26-990", "agency": "National Institutes of Health", "code": "HHS-NIH11",
     "title": "Infection Biology Pilot Projects (R21 Clinical Trial Not Allowed)",
     "text": "Pilot projects on cytomegalovirus and influenza infection in transplant "
             "recipients.", "opens": -150, "closes": -20, "ceiling": 275_000, "closed": True},
    {"number": "HS-27-990", "agency": "Agency for Healthcare Research and Quality",
     "code": "HHS-AHRQ", "title": "Patient Safety Learning Laboratories (R18)",
     "text": "Supports teams that redesign care processes to prevent harm in hospitals and "
             "clinics.", "opens": -25, "closes": 90, "ceiling": 1_000_000},
)  # fmt: skip
LOCAL_OPPORTUNITIES = (
    {"source": "Corvane Institute Office of Research", "kind": "internal",
     "title": "Translational Pilot Awards",
     "description": "One-year pilot projects that carry laboratory discoveries toward clinical "
                    "studies, especially in cellular immunotherapy, infection, and leukemia.",
     "posted_on": -20, "closes_on": 35, "award_ceiling": 100_000},
    {"source": "Hollis Family Foundation", "kind": "foundation",
     "title": "Early Investigator Awards in Neuroscience",
     "description": "Three-year awards for early-career investigators studying pain, synaptic "
                    "plasticity, and neurodegeneration.",
     "posted_on": -30, "closes_on": 75, "award_ceiling": 250_000,
     "applicant_types": "06; 20; 12"},
    {"source": "Bellweather Trust", "kind": "foundation",
     "title": "Bellweather Scholars in Biomedical Sciences",
     "description": "Five-year support for early-career scientists in any biomedical field. One "
                    "nominee per institution, chosen by an internal competition.",
     "posted_on": -10, "closes_on": 95, "internal_deadline": 20, "award_ceiling": 500_000,
     "limited_submission": True},
)  # fmt: skip
