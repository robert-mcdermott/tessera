"""A small MeSH for the demo organization: its headings, placed under broader ones, with the
synonyms people write.

The real MeSH has some 31,000 headings; the demo needs only its own 94 and the branches above
them. Heading names and the top-level branches are MeSH's; tree numbers below the branches
are numbered for the demo, and identifiers are the demo's invented ones (see world.mesh_ui).
"""

from __future__ import annotations

from typing import Any

from tessera.demo.world import mesh_ui

# The branches the demo's headings sit under: (tree number, name, synonyms).
BRANCHES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("A08", "Nervous System", ()),
    ("A11", "Cells", ()),
    ("A15", "Hemic and Immune Systems", ()),
    ("B04", "Viruses", ()),
    ("C01", "Infections", ()),
    ("C04", "Neoplasms", ("Cancer", "Cancers", "Tumor", "Tumors", "Malignancy", "Malignant Neoplasms")),
    ("C10", "Nervous System Diseases", ()),
    ("C15", "Hemic and Lymphatic Diseases", ()),
    ("C18", "Nutritional and Metabolic Diseases", ()),
    ("C20", "Immune System Diseases", ()),
    ("D12", "Amino Acids, Peptides, and Proteins", ()),
    ("D20", "Complex Mixtures", ()),
    ("D27", "Chemical Actions and Uses", ()),
    ("E01", "Diagnosis", ()),
    ("E02", "Therapeutics", ()),
    ("E05", "Investigative Techniques", ()),
    ("F01", "Behavior and Behavior Mechanisms", ()),
    ("F02", "Psychological Phenomena", ()),
    ("G02", "Chemical Phenomena", ()),
    ("G04", "Cell Physiological Phenomena", ()),
    ("G05", "Genetic Phenomena", ()),
    ("G06", "Microbiological Phenomena", ()),
    ("G07", "Physiological Phenomena", ()),
    ("G11", "Musculoskeletal and Neural Physiological Phenomena", ()),
    ("G12", "Immune System Phenomena", ()),
    ("H01", "Natural Science Disciplines", ()),
    ("L01", "Information Science", ()),
    ("M01", "Persons", ()),
    ("N01", "Population Characteristics", ()),
    ("N05", "Health Care Quality, Access, and Evaluation", ()),
)  # fmt: skip

# Where each of the demo's headings sits (one or more branches).
PLACE: dict[str, tuple[str, ...]] = {
    "CD8-Positive T-Lymphocytes": ("A11", "A15"), "Immunologic Memory": ("G12",),
    "Lymphocyte Activation": ("G12",), "Germinal Center": ("A15",), "B-Lymphocytes": ("A11", "A15"),
    "Mucosal Immunity": ("G12",), "Receptors, Antigen, T-Cell": ("D12",), "Interleukin-7": ("D12",),
    "Antigen Presentation": ("G12",), "Cell Differentiation": ("G04",), "Cytokines": ("D12",),
    "T-Lymphocytes, Regulatory": ("A11", "A15"), "HIV-1": ("B04",), "HIV Infections": ("C01", "C20"),
    "Antibodies, Neutralizing": ("D12",), "Vaccines": ("D20",), "Influenza, Human": ("C01",),
    "Virus Replication": ("G06",), "Host-Pathogen Interactions": ("G06",),
    "Drug Resistance, Viral": ("G06",), "Viral Load": ("G06",), "Antiviral Agents": ("D27",),
    "Cytomegalovirus Infections": ("C01",), "Viral Envelope Proteins": ("D12",),
    "Leukemia, Myeloid, Acute": ("C04", "C15"), "Clonal Hematopoiesis": ("C15",),
    "Genomics": ("H01",), "DNA Repair": ("G05",), "Tumor Suppressor Proteins": ("D12",),
    "Gene Expression Regulation, Neoplastic": ("G05",), "Exome Sequencing": ("E05",),
    "Chromosomal Instability": ("G05",), "Breast Neoplasms": ("C04",), "Precision Medicine": ("E02",),
    "Biomarkers, Tumor": ("D27",), "Neoplasm Metastasis": ("C04",), "Immunotherapy, Adoptive": ("E02",),
    "Receptors, Chimeric Antigen": ("D12",), "Tumor Microenvironment": ("C04",),
    "Immune Checkpoint Inhibitors": ("D27",), "T-Lymphocytes, Cytotoxic": ("A11", "A15"),
    "Multiple Myeloma": ("C04", "C15"), "Lymphoma, Large B-Cell, Diffuse": ("C04", "C15"),
    "Melanoma": ("C04",), "Cytokine Release Syndrome": ("C20",), "Tumor Escape": ("G12",),
    "Antigens, Neoplasm": ("D12",), "Neurons": ("A08", "A11"), "Synapses": ("A08",),
    "Hippocampus": ("A08",), "Memory": ("F02",), "Neuronal Plasticity": ("G11",),
    "Microglia": ("A08", "A11"), "Astrocytes": ("A08", "A11"), "Alzheimer Disease": ("C10",),
    "Sleep": ("G11",), "Neural Pathways": ("A08",), "Brain": ("A08",),
    "Neurodegenerative Diseases": ("C10",), "Cryoelectron Microscopy": ("E05",),
    "Protein Conformation": ("G02",), "Membrane Proteins": ("D12",), "Ion Channels": ("D12",),
    "Receptors, G-Protein-Coupled": ("D12",), "Protein Folding": ("G02",),
    "Allosteric Regulation": ("G02",), "Molecular Dynamics Simulation": ("E05",),
    "Protein Binding": ("G02",), "Crystallography, X-Ray": ("E05",), "Protein Multimerization": ("G02",),
    "Computational Biology": ("H01", "L01"), "Machine Learning": ("L01",),
    "Gene Regulatory Networks": ("G05",), "Single-Cell Analysis": ("E05",), "Transcriptome": ("G05",),
    "Algorithms": ("L01",), "Chromatin": ("A11",), "Epigenomics": ("G05", "H01"),
    "Sequence Analysis, RNA": ("E05",), "Deep Learning": ("L01",), "Gene Expression Profiling": ("E05",),
    "Software": ("L01",), "Colorectal Neoplasms": ("C04",), "Early Detection of Cancer": ("E01",),
    "Mass Screening": ("E01", "N05"), "Risk Factors": ("N05",), "Diet": ("G07",), "Exercise": ("G11",),
    "Obesity": ("C18",), "Health Equity": ("N01",), "Socioeconomic Factors": ("N01",),
    "Cancer Survivors": ("M01",), "Smoking": ("F01",), "Health Services Accessibility": ("N05",),
}  # fmt: skip

SYNONYMS: dict[str, tuple[str, ...]] = {
    "Leukemia, Myeloid, Acute": ("AML", "Acute Myeloid Leukemia"),
    "Breast Neoplasms": ("Breast Cancer", "Breast Tumors"),
    "Colorectal Neoplasms": ("Colorectal Cancer", "Colon Cancer"),
    "Multiple Myeloma": ("Myeloma",),
    "Lymphoma, Large B-Cell, Diffuse": ("DLBCL", "Diffuse Large B-Cell Lymphoma"),
    "HIV Infections": ("HIV Infection", "HIV Disease"),
    "HIV-1": ("HIV",),
    "Alzheimer Disease": ("Alzheimer's Disease", "Alzheimer Dementia"),
    "Cryoelectron Microscopy": ("Cryo-EM", "Cryo-Electron Microscopy"),
    "Receptors, Chimeric Antigen": ("Chimeric Antigen Receptors",),
    "Immunotherapy, Adoptive": ("Adoptive Cell Therapy", "CAR T-Cell Therapy"),
    "Influenza, Human": ("Flu", "Influenza"),
    "Exercise": ("Physical Activity",),
    "Mass Screening": ("Screening",),
    "Smoking": ("Tobacco Smoking",),
    "CD8-Positive T-Lymphocytes": ("CD8-Positive T Cells", "CD8 T Cells"),
    "Immunologic Memory": ("Immune Memory",),
}


def mesh_records(year: int) -> list[dict[str, Any]]:
    """Records for the ``mesh_descriptor`` table: the branches, then the demo's headings."""
    records = [
        {
            "descriptor_ui": mesh_ui(name),
            "name": name,
            "descriptor_class": 1,
            "tree_numbers": [tree],
            "entry_terms": list(terms),
            "scope_note": None,
            "mesh_year": year,
            "last_updated": None,
        }
        for tree, name, terms in BRANCHES
    ]
    numbered: dict[str, int] = {}
    for name in sorted(PLACE):
        trees = []
        for branch in PLACE[name]:
            numbered[branch] = numbered.get(branch, 0) + 1
            trees.append(f"{branch}.{numbered[branch] * 10:03d}")
        records.append(
            {
                "descriptor_ui": mesh_ui(name),
                "name": name,
                "descriptor_class": 1,
                "tree_numbers": trees,
                "entry_terms": list(SYNONYMS.get(name, ())),
                "scope_note": None,
                "mesh_year": year,
                "last_updated": None,
            }
        )
    return records
