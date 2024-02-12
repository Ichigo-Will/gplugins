from gdsfactory.technology import LayerStack
from gdsfactory.typings import PathType
from gdsfactory.pdk import get_layer_stack
from gdsfactory.config import logger
from xml.etree.ElementTree import Element, SubElement
from xml.dom import minidom
import xml.etree.ElementTree as ET
from pathlib import Path

um = 1e-6


def to_lbr(
    material_map: dict[str, str],
    layerstack: LayerStack | None = None,
    dirpath: PathType | None = "",
) -> None:
    """
    Generate an XML file representing a Lumerical Layer Builder process file based on provided material map.

    Args:
        material_map: A dictionary mapping materials used in the layer stack to Lumerical materials.
        layerstack: Layer stack that has info on layer names, layer numbers, thicknesses, etc.
        dirpath: Directory to save process file (process.lbr)

    Returns:
        Process file path

    Notes:
        This function generates an XML file representing a Layer Builder file for Lumerical, based on the provided the active PDK
        and material map. It creates 'process.lbr' in the current working directory, containing layer information like name,
        material, thickness, sidewall angle, and other properties specified in the layer stack. It skips layers that are not
        of type 'grow' or 'background' and logs a warning for each skipped layer.
    """
    layerstack = layerstack or get_layer_stack()

    layer_builder = Element("layer_builder")

    process_name = SubElement(layer_builder, "process_name")
    process_name.text = "process"

    layers = SubElement(layer_builder, "layers")
    doping_layers = SubElement(layer_builder, "doping_layers")
    for layer_name, layer_info in layerstack.to_dict().items():
        ### Set optical and metal layers
        if layer_info["layer_type"] == "grow":
            process = "Grow"
        elif layer_info["layer_type"] == "background":
            process = "Background"
        elif layer_info["layer_type"] == "doping":
            process = "Implant"
        else:
            logger.warning(
                f'"{layer_info["layer_type"]}" layer type not supported for "{layer_name}" in Lumerical. Skipping in LBR process file generation.'
            )
            process = "Grow"

        if process == "Grow" or process == "Background":
            layer = SubElement(layers, "layer")

            # Default params
            layer.set("enabled", "1")
            layer.set("pattern_alpha", "0.8")
            layer.set("start_position_auto", "0")
            layer.set("background_alpha", "0.3")
            layer.set("pattern_material_index", "0")
            layer.set("material_index", "0")

            # Layer specific params
            layer.set("name", layer_name)
            layer.set(
                "layer_name", f'{layer_info["layer"][0]}:{layer_info["layer"][1]}'
            )
            layer.set("start_position", f'{layer_info["zmin"] * um}')
            layer.set("thickness", f'{layer_info["thickness"] * um}')
            layer.set("start_position", f'{layer_info["zmin"] * um}')
            layer.set("process", f"{process}")
            layer.set("sidewall_angle", f'{90-layer_info["sidewall_angle"]}')
            if layer_info["bias"]:
                layer.set("pattern_growth_delta", f"{layer_info['bias'] * um}")
            else:
                layer.set("pattern_growth_delta", "0")

            if process == "Grow":
                layer.set(
                    "pattern_material",
                    f'{material_map.get(layer_info["material"], "")}',
                )
            elif process == "Background":
                layer.set("material", f'{material_map.get(layer_info["material"], "")}')

        if process == "Implant" or process == "Background":
            ### Set doping layers
            # KNOWN ISSUE: If a metal or optical layer has the same name as a doping layer, Layer Builder will not compile
            # the process file correctly and the doping layer will not appear. Therefore, doping layer names MUST be unique.
            # FIX: Appending "_doping" to name

            # KNOWN ISSUE: If the 'process' is not 'Background' or 'Implant', this will crash CHARGE upon importing process file.
            # FIX: Ensure process is Background or Implant before proceeding to create entry

            # KNOWN ISSUE: Dopant must be either 'p' or 'n'. Anything else will cause CHARGE to crash upon importing process file.
            # FIX: Raise ValueErrorr when dopant is specified incorrectly
            if layer_info.get(
                "background_doping_concentration", False
            ) and layer_info.get("background_doping_ion", False):
                doping_layer = SubElement(doping_layers, "layer")

                # Order of param matters
                doping_layer.set("z_surface_positions", f'{layer_info["zmin"] * um}')
                doping_layer.set("distribution_function", "Gaussian")
                doping_layer.set("phi", "0")
                doping_layer.set("lateral_scatter", "2e-08")
                doping_layer.set("range", f"{layer_info['thickness'] * um}")
                doping_layer.set("theta", "0")
                if (
                    layer_info["background_doping_ion"] == "n"
                    or layer_info["background_doping_ion"] == "p"
                ):
                    doping_layer.set("dopant", layer_info["background_doping_ion"])
                else:
                    raise ValueError(
                        f'Dopant must be "p" or "n". Got {layer_info["background_doping_ion"]}.'
                    )
                doping_layer.set(
                    "mask_layer_number",
                    f'{layer_info["layer"][0]}:{layer_info["layer"][1]}',
                )
                doping_layer.set("kurtosis", "0")
                doping_layer.set("process", f"{process}")
                doping_layer.set("skewness", "0")
                doping_layer.set("straggle", "4.9999999999999998e-08")
                doping_layer.set(
                    "concentration", f"{layer_info['background_doping_concentration']}"
                )
                doping_layer.set("enabled", "1")
                doping_layer.set("name", f"{layer_name}_doping")

    # If no doping layers exist, delete element
    if len(doping_layers) == 0:
        layer_builder.remove(doping_layers)

    # Prettify XML
    rough_string = ET.tostring(layer_builder, "utf-8")
    reparsed = minidom.parseString(rough_string)
    xml_str = reparsed.toprettyxml(indent="  ")

    if dirpath:
        process_file_path = Path(str(dirpath)) / "process.lbr"
    else:
        process_file_path = Path(__file__).resolve().parent / "process.lbr"
    with open(str(process_file_path), "w") as f:
        f.write(xml_str)

    return process_file_path