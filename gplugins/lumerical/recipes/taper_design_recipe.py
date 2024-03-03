import hashlib
import typing
from functools import partial
from pathlib import Path

import gdsfactory as gf
import numpy as np
import pandas as pd
from gdsfactory import Component
from gdsfactory.components.taper_cross_section import taper_cross_section
from gdsfactory.config import logger
from gdsfactory.pdk import LayerStack, get_layer_stack
from gdsfactory.typings import ComponentFactory, CrossSectionSpec, PathType, WidthTypes
from pydantic import BaseModel

from gplugins.design_recipe.DesignRecipe import DesignRecipe, eval_decorator
from gplugins.lumerical.convergence_settings import (
    LUMERICAL_EME_CONVERGENCE_SETTINGS,
    ConvergenceSettingsLumericalEme,
    ConvergenceSettingsLumericalFdtd,
)
from gplugins.lumerical.eme import LumericalEmeSimulation
from gplugins.lumerical.recipes.fdtd_recipe import FdtdRecipe
from gplugins.lumerical.simulation_settings import (
    LUMERICAL_EME_SIMULATION_SETTINGS,
    SimulationSettingsLumericalEme,
    SimulationSettingsLumericalFdtd,
)

um = 1e-6
cm = 1e-2


def example_run_taper_design_recipe():
    ### 0. DEFINE WHERE FILES ARE SAVED
    dirpath = Path(__file__).parent / "example_runs" / "taper_design_recipe"
    dirpath.mkdir(parents=True, exist_ok=True)

    ### 1. DEFINE DESIGN INTENT
    design_intent = RoutingTaperDesignIntent(
        narrow_waveguide_routing_loss_per_cm=3,  # dB/cm
    )

    narrow_waveguide_cross_section = partial(
        gf.cross_section.cross_section,
        layer=(1, 0),
        width=0.5,
    )
    wide_waveguide_cross_section = partial(
        gf.cross_section.cross_section,
        layer=(1, 0),
        width=3.0,
    )

    ### 2. DEFINE LAYER STACK AND MATERIAL MAPPING FROM PDK TO LUMERICAL
    layer_map = {
        "si": "Si (Silicon) - Palik",
        "sio2": "SiO2 (Glass) - Palik",
        "sin": "Si3N4 (Silicon Nitride) - Phillip",
        "TiN": "TiN - Palik",
        "Aluminum": "Al (Aluminium) Palik",
    }

    from gdsfactory.technology.layer_stack import LayerLevel, LayerStack

    layerstack_lumerical = LayerStack(
        layers={
            "clad": LayerLevel(
                layer=(99999, 0),
                thickness=3.0,
                zmin=0.0,
                material="sio2",
                sidewall_angle=0.0,
                mesh_order=9,
                layer_type="background",
            ),
            "box": LayerLevel(
                layer=(99999, 0),
                thickness=3.0,
                zmin=-3.0,
                material="sio2",
                sidewall_angle=0.0,
                mesh_order=9,
                layer_type="background",
            ),
            "core": LayerLevel(
                layer=(1, 0),
                thickness=0.22,
                zmin=0.0,
                material="si",
                sidewall_angle=2.0,
                width_to_z=0.5,
                mesh_order=2,
                layer_type="grow",
                info={"active": True},
            ),
        }
    )

    ### 3. DEFINE SIMULATION AND CONVERGENCE SETTINGS
    eme_convergence_setup = ConvergenceSettingsLumericalEme(
        sparam_diff=1 - 10 ** (-0.005 / 10)
    )
    eme_simulation_setup = SimulationSettingsLumericalEme()

    fdtd_convergence_setup = ConvergenceSettingsLumericalFdtd(
        port_field_intensity_threshold=1e-6, sparam_diff=0.01
    )
    fdtd_simulation_setup = SimulationSettingsLumericalFdtd(
        mesh_accuracy=2, port_translation=1.0
    )

    ### 4. CREATE AND RUN DESIGN RECIPE
    eme_recipe = RoutingTaperDesignRecipe(
        cell=taper_cross_section,
        cross_section1=narrow_waveguide_cross_section,
        cross_section2=wide_waveguide_cross_section,
        design_intent=design_intent,
        material_map=layer_map,
        layer_stack=layerstack_lumerical,
        simulation_setup=eme_simulation_setup,
        convergence_setup=eme_convergence_setup,
        dirpath=dirpath,
    )
    success = eme_recipe.eval()

    fdtd_recipe = FdtdRecipe(
        component=eme_recipe.component,
        material_map=layer_map,
        layer_stack=layerstack_lumerical,
        simulation_setup=fdtd_simulation_setup,
        convergence_setup=fdtd_convergence_setup,
        dirpath=dirpath,
    )
    success = success and fdtd_recipe.eval()

    if success:
        logger.info("Completed taper design recipe.")
    else:
        logger.info("Incomplete run of taper design recipe.")


class RoutingTaperDesignIntent(BaseModel):
    r"""
    Design intent for routing taper design recipe

    Attributes:
        narrow_waveguide_routing_loss_per_cm: Narrow waveguide routing loss (dB/cm)
        max_reflection: Maximum reflections to
        start_length: Starting length in length sweep (um)
        stop_length: Ending length in length sweep (um)
        num_pts: Number of points to consider in length sweep

                         |       |
                         |      /|---------
                         |    /  |
                         |  /    |
        -----------------|/      |
        cross section 1  | taper | cross section 2
        -----------------|\      |
                         |  \    |
                         |    \  |
                         |      \|---------
                         |       |

    """

    narrow_waveguide_routing_loss_per_cm: float = 3  # dB / cm
    max_reflection: float = -70  # dB

    # Length Sweep
    start_length: float = 1  # um
    stop_length: float = 200  # um
    num_pts: int = 200  # um

    class Config:
        arbitrary_types_allowed = True


class RoutingTaperDesignRecipe(DesignRecipe):
    """
    Routing taper design recipe.
    """

    # Design intent
    design_intent: RoutingTaperDesignIntent | None = None

    # Setup
    simulation_setup: SimulationSettingsLumericalEme | None = (
        LUMERICAL_EME_SIMULATION_SETTINGS
    )
    convergence_setup: ConvergenceSettingsLumericalEme | None = (
        LUMERICAL_EME_CONVERGENCE_SETTINGS
    )

    # Results
    component: Component | None = None  # Optimal taper component
    length_sweep: pd.DataFrame | None = None  # Length sweep results

    def __init__(
        self,
        cell: ComponentFactory = taper_cross_section,
        cross_section1: CrossSectionSpec | None = gf.cross_section.cross_section,
        cross_section2: CrossSectionSpec | None = gf.cross_section.cross_section,
        design_intent: RoutingTaperDesignIntent | None = None,
        material_map: dict[str, str] | None = None,
        layer_stack: LayerStack | None = None,
        simulation_setup: SimulationSettingsLumericalEme
        | None = LUMERICAL_EME_SIMULATION_SETTINGS,
        convergence_setup: ConvergenceSettingsLumericalEme
        | None = LUMERICAL_EME_CONVERGENCE_SETTINGS,
        dirpath: PathType | None = None,
    ):
        layer_stack = layer_stack or get_layer_stack()
        super().__init__(cell=cell, material_map=material_map, layer_stack=layer_stack)
        self.cross_section1 = cross_section1
        self.cross_section2 = cross_section2
        self.dirpath = dirpath or Path(__file__).resolve().parent
        self.design_intent = design_intent or RoutingTaperDesignIntent()
        self.simulation_setup = simulation_setup
        self.convergence_setup = convergence_setup

    def __hash__(self) -> int:
        """
        Returns a hash of all state and setup this DesignRecipe contains.
        This is used to determine 'freshness' of a recipe (i.e. if it needs to be rerun)

        Hashed items:
        - design intent
        - simulation setup
        - convergence setup
        """
        h = hashlib.sha1()
        int_hash = super().__hash__()
        h.update(int_hash.to_bytes(int_hash.bit_length() + 7 // 8, byteorder="big"))
        h.update(self.simulation_setup.model_dump_json().encode("utf-8"))
        h.update(self.convergence_setup.model_dump_json().encode("utf-8"))
        h.update(self.design_intent.model_dump_json().encode("utf-8"))
        return int.from_bytes(h.digest(), "big")

    @eval_decorator
    def eval(self):
        r"""
        Run taper design recipe.

                         |       |
                         |      /|---------
                         |    /  |
                         |  /    |
        -----------------|/      |
        cross section 1  | taper | cross section 2
        -----------------|\      |
                         |  \    |
                         |    \  |
                         |      \|---------
                         |       |

        1. Sweep taper geometry in EME and get best geometry and length for component.
                Best component is derived from the following (in order):
                a) The dB/cm loss for the narrow waveguide routing must match the derived dB/cm loss for the taper
                b) The component must have the lowest reflections
                c) The component must be the shortest
        2. Run FDTD simulation to extract s-params for best component
        """
        self.last_hash = hash(self)
        ss = self.simulation_setup
        cs = self.convergence_setup
        di = self.design_intent

        # Sweep geometry
        components = [
            self.cell(
                cross_section1=self.cross_section1,
                cross_section2=self.cross_section2,
                length=5,  # um
                width_type=wtype,
            )
            for wtype in typing.get_args(WidthTypes)
        ]

        optimal_lengths = []
        transmission_coefficients = []
        reflection_coefficients = []
        simulated_components = []
        sims = []
        length_sweeps = []
        for component in components:
            try:
                sim = LumericalEmeSimulation(
                    component=component,
                    material_map=self.material_map,
                    layerstack=self.layer_stack,
                    simulation_settings=ss,
                    convergence_settings=cs,
                    hide=False,  # TODO: Make global variable for switching debug modes
                    run_overall_convergence=True,
                    run_mesh_convergence=False,
                    run_mode_convergence=True,
                    run_cell_convergence=False,
                    dirpath=self.dirpath,
                )
                sims.append(sim)
                simulated_components.append(component)
            except Exception as err:
                logger.error(
                    f"{err}\n{component.name} failed to simulate. Moving onto next component"
                )
                continue

            # Get length of taper that has lower loss than routing loss
            length_sweep = sim.get_length_sweep(
                start_length=di.start_length,
                stop_length=di.stop_length,
                num_pts=di.num_pts,
            )
            length_sweeps.append(length_sweep)

            length = length_sweep.loc[:, "length"]
            s21 = 10 * np.log10(abs(length_sweep.loc[:, "s21"]) ** 2)
            s11 = 10 * np.log10(abs(length_sweep.loc[:, "s11"]) ** 2)
            try:
                ind = next(
                    k
                    for k, value in enumerate(list(s21))
                    if value > -di.narrow_waveguide_routing_loss_per_cm * length[k] / cm
                )
                optimal_lengths.append(length[ind] / um)
                transmission_coefficients.append(s21[ind])
                reflection_coefficients.append(s11[ind])
            except StopIteration:
                logger.warning(
                    f"{component.name} cannot achieve specified routing loss of "
                    + f"-{di.narrow_waveguide_routing_loss_per_cm}dB/cm. Use maximal length of {di.stop_length}um."
                )
                optimal_lengths.append(di.stop_length)
                transmission_coefficients.append(s21[-1])
                reflection_coefficients.append(s11[-1])

        results = {
            f"{components[i].name} ({components[i].settings.get('width_type', 'Shape Unknown')})": f"L: {optimal_lengths[i]} | T: {transmission_coefficients[i]} | R: {reflection_coefficients[i]}"
            for i in range(0, len(simulated_components))
        }
        logger.info(f"{results}")

        # Get best component
        # Most optimal component is one with smallest length AND least reflections
        # If not both, choose component with lower than specified reflection or least reflections
        ind1 = optimal_lengths.index(min(optimal_lengths))
        ind2 = reflection_coefficients.index(min(reflection_coefficients))

        if ind1 == ind2 or (reflection_coefficients[ind1] < di.max_reflection):
            # Select shortest component and minimal reflections. Else, shortest component with reflection below specified
            optimal_component = self.cell(
                cross_section1=self.cross_section1,
                cross_section2=self.cross_section2,
                length=optimal_lengths[ind1],  # um
                width_type=list(typing.get_args(WidthTypes))[ind1],
            )
            opt_length_sweep_data = length_sweeps[ind1]
        else:
            # Select component with minimal reflections
            optimal_component = self.cell(
                cross_section1=self.cross_section1,
                cross_section2=self.cross_section2,
                length=optimal_lengths[ind2],  # um
                width_type=list(typing.get_args(WidthTypes))[ind2],
            )
            opt_length_sweep_data = length_sweeps[ind2]

        # Save results
        self.component = optimal_component
        self.length_sweep = opt_length_sweep_data


if __name__ == "__main__":
    example_run_taper_design_recipe()
    # layer_map = {
    #     "si": "Si (Silicon) - Palik",
    #     "sio2": "SiO2 (Glass) - Palik",
    #     "sin": "Si3N4 (Silicon Nitride) - Phillip",
    #     "TiN": "TiN - Palik",
    #     "Aluminum": "Al (Aluminium) Palik",
    # }
    # from gdsfactory.technology.layer_stack import LayerLevel, LayerStack
    #
    # layerstack_lumerical2021 = LayerStack(
    #     layers={
    #         "clad": LayerLevel(
    #             name=None,
    #             layer=(99999, 0),
    #             thickness=3.0,
    #             thickness_tolerance=None,
    #             zmin=0.0,
    #             zmin_tolerance=None,
    #             material="sio2",
    #             sidewall_angle=0.0,
    #             sidewall_angle_tolerance=None,
    #             width_to_z=0.0,
    #             z_to_bias=None,
    #             mesh_order=9,
    #             layer_type="background",
    #             mode=None,
    #             into=None,
    #             resistivity=None,
    #             bias=None,
    #             derived_layer=None,
    #             info={},
    #             background_doping_concentration=None,
    #             background_doping_ion=None,
    #             orientation="100",
    #         ),
    #         "box": LayerLevel(
    #             name=None,
    #             layer=(99999, 0),
    #             thickness=3.0,
    #             thickness_tolerance=None,
    #             zmin=-3.0,
    #             zmin_tolerance=None,
    #             material="sio2",
    #             sidewall_angle=0.0,
    #             sidewall_angle_tolerance=None,
    #             width_to_z=0.0,
    #             z_to_bias=None,
    #             mesh_order=9,
    #             layer_type="background",
    #             mode=None,
    #             into=None,
    #             resistivity=None,
    #             bias=None,
    #             derived_layer=None,
    #             info={},
    #             background_doping_concentration=None,
    #             background_doping_ion=None,
    #             orientation="100",
    #         ),
    #         "core": LayerLevel(
    #             name=None,
    #             layer=(1, 0),
    #             thickness=0.22,
    #             thickness_tolerance=None,
    #             zmin=0.0,
    #             zmin_tolerance=None,
    #             material="si",
    #             sidewall_angle=2.0,
    #             sidewall_angle_tolerance=None,
    #             width_to_z=0.5,
    #             z_to_bias=None,
    #             mesh_order=2,
    #             layer_type="grow",
    #             mode=None,
    #             into=None,
    #             resistivity=None,
    #             bias=None,
    #             derived_layer=None,
    #             info={"active": True},
    #             background_doping_concentration=100000000000000.0,
    #             background_doping_ion="Boron",
    #             orientation="100",
    #         ),
    #         # KNOWN ISSUE: Lumerical 2021 version of Layer Builder does not support dopants in process file
    #     }
    # )
    #
    # LUMERICAL_EME_CONVERGENCE_SETTINGS.sparam_diff = 1 - 10 ** (-0.0025 / 10)
    # taper_recipe = RoutingTaperDesignRecipe(
    #     material_map=layer_map,
    #     simulation_setup=LUMERICAL_EME_SIMULATION_SETTINGS,
    #     convergence_setup=LUMERICAL_EME_CONVERGENCE_SETTINGS,
    #     layer_stack=layerstack_lumerical2021,
    # )
    # success = taper_recipe.eval()
    # logger.info("Done")
