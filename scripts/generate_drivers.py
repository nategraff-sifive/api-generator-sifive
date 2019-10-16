#!/usr/bin/env python3.7

import argparse
import json
import string
import sys
import textwrap
import typing as t
from dataclasses import dataclass
from pathlib import Path

import json5

###
# JSON support
###

PlainJSONType = t.Union[dict, list, t.AnyStr, float, bool]
JSONType = t.Union[PlainJSONType, t.Iterator[PlainJSONType]]


def walk(j_obj: JSONType) -> t.Iterator[JSONType]:
    """
    Walk a parsed json object, returning inner nodes.
    This allows the object to be parse in a pipeline like fashion.

    :param j_obj: The object being parsed, or an iterator
    :return: an iterator of matching objects
    """
    if isinstance(j_obj, dict):
        yield j_obj
        for v in j_obj.values():
            yield from walk(v)
    elif isinstance(j_obj, (list, t.Iterator)):
        yield j_obj
        for j in j_obj:
            yield from walk(j)


@dataclass(frozen=True)
class Register:
    """
    Description of memory-mapped control register within a device
    """
    name: str
    offset: int  # in bytes
    width: int  # in bits

    @staticmethod
    def make_register(name: str, offset: int, width: int) -> "Register":
        if width not in (8, 16, 32, 64):
            raise Exception(f'Invalid register width {width}, for register '
                            f'{name}.\n'
                            f'Width should be not 8, 16, 32, or 64.\n'
                            f'Please fix the register width in DUH document.')
        return Register(name, offset, width)


###
# templates
###

def generate_vtable_declarations(device_name: str,
                                 reg_list: t.List[Register]) -> str:
    """
    Generate the vtable entries for a device and set of registers. This
    creates the declarations for function pointers for all the driver functions.
    This is used to provide a single point for all functions that can be used
    for multiple devices.
    :param device_name: the name of the device
    :param reg_list: a list of Register objects for the device
    :return: the c code for the vtable entries
    """

    rv = []

    for a_reg in reg_list:
        reg_name = a_reg.name.lower()
        size = a_reg.width

        write_func = f'    void (*v_{device_name}_{reg_name}_write)(uint32_t * {device_name}_base, uint{size}_t data);'
        read_func = f'    uint{size}_t (*v_{device_name}_{reg_name}_read)(uint32_t  *{device_name}_base);'

        rv.append(write_func)
        rv.append(read_func)

    return '\n'.join(rv)


def generate_metal_vtable_definition(devices_name: str) -> str:
    """
    Generate the vtable and base address variable definitions
    for the given device name

    :param devices_name:
    :return: The c code for the metal device
    """

    return f'    uint32_t *{devices_name}_base;\n' + \
           f'    const struct metal_{devices_name}_vtable vtable;'


def generate_protos(device_name: str, reg_list: t.List[Register]) -> str:
    """
    Generate the function prototypes for a given device and register list.

    :param device_name: The device name
    :param reg_list: the list of registers for the device
    :return: the c language prototypes for the device
    """

    rv = []

    dev_struct = f'const struct metal_{device_name} *{device_name}'

    for a_reg in reg_list:
        reg_name = a_reg.name.lower()
        size = a_reg.width

        write_func = f'void metal_{device_name}_{reg_name}_write({dev_struct}, uint{size}_t data);'
        read_func = f'uint{size}_t metal_{device_name}_{reg_name}_read({dev_struct});'

        rv.append(write_func)
        rv.append(read_func)

    get_device = f'const struct metal_{device_name} *get_metal_{device_name}' \
                 f'(uint8_t index);'
    rv.append(get_device)

    return '\n'.join(rv)


###
# templates
###

METAL_BASE_HDR_TMPL = \
    """
    #include <metal/compiler.h>
    #include <metal/io.h>

    #ifndef ${vendor}_${device}_h
    #define ${vendor}_${device}_h
    #define ${cap_device}_BASE ${base_address}

    // : these macros have control_base as a hidden input
    #define METAL_${cap_device}_REG(offset) (((unsigned long)control_base + offset))
    #define METAL_${cap_device}_REGW(offset) \\
       (__METAL_ACCESS_ONCE((__metal_io_u32 *)METAL_${cap_device}_REG(offset)))

    ${register_offsets}

    #endif
    """


def generate_offsets(device_name: str, reg_list: t.List[Register]) -> str:
    """
    Generate the register offset macros

    :param device_name: the name of the device
    :param reg_list: the list of registers for the device.
    :return:The offset c macros for the device and registers
    """
    rv: t.List[str] = []

    cap_device = device_name.upper()
    for a_reg in reg_list:
        name = a_reg.name.upper()
        offset = a_reg.offset
        macro_line = f'#define METAL_{cap_device}_{name} {offset}'
        rv.append(macro_line)

    return '\n'.join(rv)


def generate_base_hdr(vendor: str,
                      device: str,
                      base_address: int,
                      reglist: t.List[Register]):
    template = string.Template(textwrap.dedent(METAL_BASE_HDR_TMPL))

    return template.substitute(
        base_address=hex(base_address),
        vendor=vendor,
        device=device,
        cap_device=device.upper(),
        register_offsets=generate_offsets(device, reglist)
    )


# The template for the .h file

METAL_DEV_HDR_TMPL = \
    """
    #include <metal/compiler.h>
    #include <stdint.h>
    #include <stdlib.h>
    #include <bsp_${device}/${vendor}_${device}.h>

    #ifndef ${vendor}_${device}${index}_h
    #define ${vendor}_${device}${index}_h

    struct metal_${device};

    struct metal_${device}_vtable {
    ${vtable}
    };

    struct metal_${device} {
    ${metal_device}
    };

    __METAL_DECLARE_VTABLE(metal_${device})

    ${protos}
    #endif
    """


def generate_metal_dev_hdr(vendor, device, index, reglist):
    """

    :param vendor: The name of the vendor creating the device
    :param device: the name of the device created.
    :param index: the index of the device
    :param reglist: the list of registers for the device
    :return: a string which is the .h for file the device driver
    """
    template = string.Template(textwrap.dedent(METAL_DEV_HDR_TMPL))

    return template.substitute(
        vendor=vendor,
        device=device,
        cap_device=device.upper(),
        index=str(index),
        #base_address=hex(base_address),
        vtable=generate_vtable_declarations(device, reglist),
        metal_device=generate_metal_vtable_definition(device),
        protos=generate_protos(device, reglist)
    )


# the template for the driver .c file
METAL_DEV_DRV_TMPL = \
    """
    #include <stdint.h>
    #include <stdlib.h>

    #include <${device}/${vendor}_${device}${index}.h>
    #include <metal/compiler.h>
    #include <metal/io.h>

    ${base_functions}

    ${metal_functions}

    __METAL_DEFINE_VTABLE(metal_${device}) = {
    ${def_vtable}
    };

    const struct metal_${device}* ${device}_tables[] = {&metal_${device}};
    uint8_t ${device}_tables_cnt = 1;

    const struct metal_${device}* get_metal_${device}(uint8_t idx)
    {
        if (idx >= ${device}_tables_cnt)
            return NULL;
        return ${device}_tables[idx];
    }
    """


def generate_def_vtable(device: str, reg_list: t.List[Register]) -> str:
    """
    Generate vtable settings for vtable declaration in .c file

    :param device: the name of the device
    :param reg_list: the register list for the device
    :return: the declarations in the vtable for the driver .c file
    """
    rv: t.List[str] = []
    cap_device = device.upper()
    head = f'    .{device}_base = (uint32_t *){cap_device}_BASE,'
    rv.append(head)
    for a_reg in reg_list:
        reg_name = a_reg.name.lower()

        write_func = f'    .vtable.v_{device}_{reg_name}_write ' \
                     f'= {device}_{reg_name}_write,'
        read_func = f'    .vtable.v_{device}_{reg_name}_read ' \
                    f'= {device}_{reg_name}_read,'
        rv.append(write_func)
        rv.append(read_func)

    return '\n'.join(rv)


def generate_base_functions(device: str, reg_list: t.List[Register]) -> str:
    """
    Generates the basic, not exported register access functions for
    a given device and register list.

    :param device: the name of the device
    :param reg_list: the list of registers for the device.
    :return:  the c code for the register access functions
    """
    cap_device = device.upper()
    rv: t.List[str] = []

    for a_reg in reg_list:
        name = a_reg.name.lower()
        cap_name = a_reg.name.upper()
        size = a_reg.width

        write_func = f"""
            void {device}_{name}_write(uint32_t *{device}_base, uint{size}_t data)
            {{
                volatile uint32_t *control_base = {device}_base;
                METAL_{cap_device}_REGW(METAL_{cap_device}_{cap_name}) = data;
            }}
            """

        rv.append(textwrap.dedent(write_func))

        read_func = f"""
            uint{size}_t {device}_{name}_read(uint32_t *{device}_base)
            {{
                volatile uint32_t *control_base = {device}_base;
                return METAL_{cap_device}_REGW(METAL_{cap_device}_{cap_name});
            }}
            """

        rv.append(textwrap.dedent(read_func))

    return '\n'.join(rv)


def generate_metal_function(device: str, reg_list: t.List[Register]) -> str:
    """
    Generates the exported register access functions for
    a given device and register list.

    :param device: the name of the device
    :param reg_list: the list of registers for the device.
    :return:  the c code for the exported register access functions
    """

    rv: t.List[str] = []

    for a_reg in reg_list:
        name = a_reg.name.lower()
        size = a_reg.width

        write_func = f"""
            void metal_{device}_{name}_write(const struct metal_{device} *{device}, uint{size}_t data)
            {{
                if ({device} != NULL)
                    {device}->vtable.v_{device}_{name}_write({device}->{device}_base, data);
            }}
            """
        rv.append(textwrap.dedent(write_func))

        read_func = f"""
            uint{size}_t metal_{device}_{name}_read(const struct metal_{device} *{device})
            {{
                if ({device} != NULL)
                    return {device}->vtable.v_{device}_{name}_read({device}->{device}_base);
                return (uint{size}_t)-1;
            }}
            """

        rv.append(textwrap.dedent(read_func))

    return '\n'.join(rv)


def generate_metal_dev_drv(vendor, device, index, reglist):
    """
    Generate the driver source file contents for a given device
    and register list

    :param vendor: the vendor creating the device
    :param device: the device
    :param index: the index of the device used
    :param reglist: the list of registers
    :return: a string containing of the c code for the basic driver
    """
    template = string.Template(textwrap.dedent(METAL_DEV_DRV_TMPL))

    return template.substitute(
        vendor=vendor,
        device=device,
        cap_device=device.upper(),
        index=str(index),
        offsets=generate_offsets(device, reglist),
        base_functions=generate_base_functions(device, reglist),
        metal_functions=generate_metal_function(device, reglist),
        def_vtable=generate_def_vtable(device, reglist)
    )


###
# main
###


def handle_args():
    """
    :return:
    """
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "-o",
        "--object-model",
        type=argparse.FileType('r'),
        help="The path to the object model file",
    )

    parser.add_argument(
        "-d",
        "--duh-document",
        type=argparse.FileType('r'),
        help="The path to the DUH document",
        required=True
    )

    parser.add_argument(
        "--vendor",
        help="The vendor name",
        required=True,
    )

    parser.add_argument(
        "-D",
        "--device",
        help="The device name",
        required=True,
    )

    parser.add_argument(
        "-m",
        "--metal-dir",
        help="The path to the drivers/metal directory",
        type=Path,
        required=True,
    )

    parser.add_argument(
        "-b",
        "--bsp-dir",
        help="The path to the bsp directory",
        type=Path,
        required=True,
    )


    parser.add_argument(
        "-x",
        "--overwrite-existing",
        action="store_true",
        default=False,
        help="overwrite existing files"
    )

    parser.add_argument(
        "-H",
        "--base-header",
        action="store_true",
        default=False,
        help="Create base header file. Require Object Model File."
    )

    parser.add_argument(
        "-I",
        "--basic-drivers",
        action="store_true",
        default=False,
        help="Create basic driver files. Requires DUH Document"
    )

    return parser.parse_args()


def main() -> int:
    """
    :return: exits 0 on success, 1 on failure
    """
    # ###
    # parse args
    # ###

    args = handle_args()

    create_base_header = args.base_header
    if create_base_header:
        if not args.object_model:
            print("Need an object model file to create base header",
                  file=sys.stderr)
            return 1

        if not args.bsp_dir:
            print("Need a BSP directory to create base header",
                  file = sys.stderr)
            return 1

        object_model = json.load(args.object_model)
        bsp_dir_path = args.bsp_dir
    else:
        object_model = None
        bsp_dir_path = None

    create_basic_drivers = args.basic_drivers
    duh_info = json5.load(args.duh_document)

    vendor = args.vendor
    device = args.device
    m_dir_path = args.metal_dir
    overwrite_existing = args.overwrite_existing

    # ###
    # process register info from duh
    # ###

    def interpret_register(a_reg: dict) -> Register:
        name = a_reg['name']
        offset = a_reg['addressOffset'] // 8
        width = a_reg['size']
        return Register.make_register(name, offset, width)

    p = walk(duh_info)
    p = filter(lambda x: 'name' in x and x['name'] == 'csrAddressBlock', p)
    p = map(lambda x: x['registers'], p)
    p = (j for i in p for j in i)  # flatten
    reglist: t.List[Register] = list(map(interpret_register, p))

    # ###
    # parse OM to find base address of all devices
    # ###
    if create_base_header:
        p = walk(object_model)
        p = filter(lambda x: '_types' in x, p)
        p = filter(lambda x: 'OMMemoryRegion' in x['_types'], p)
        p = filter(lambda x: 'name' in x, p)
        p = filter(lambda x: x['name'].startswith(device), p)
        devices_addr_enumerated = enumerate(p)
    else:
        devices_addr_enumerated = None


    # ###
    # Base Headers
    # ###

    if create_base_header:
        base_hdr_path = bsp_dir_path / f'bsp_{device}'
        base_hdr_path.mkdir(exist_ok=True, parents=True)
        for index, om in devices_addr_enumerated:
            base = om['addressSets'][0]['base']
            print(base)
            if create_base_header:
                base_header_file_path = base_hdr_path / f'{vendor}_{device}.h'
                if  overwrite_existing or not base_header_file_path.exists():
                    base_header_file_path.write_text(
                        generate_base_hdr(vendor, device, base, reglist)
                    )
                else:
                    print(f"{str(base_header_file_path)} exists, not creating.",
                          file=sys.stderr)

    # ###
    # basic drivers
    # ###

    if create_basic_drivers:
        m_hdr_path = m_dir_path / device
        m_hdr_path.mkdir(exist_ok=True, parents=True)

        driver_file_path = m_dir_path / f'{vendor}_{device}.c'
        header_file_path = m_hdr_path / f'{vendor}_{device}{0}.h'

        if overwrite_existing or not driver_file_path.exists():
            driver_file_path.write_text(
                generate_metal_dev_drv(vendor, device, 0, reglist))
        else:
            print(f"{str(driver_file_path)} exists, not creating.",
                  file=sys.stderr)

        if overwrite_existing or  not header_file_path.exists():
            header_file_path.write_text(
                generate_metal_dev_hdr(vendor, device, 0, reglist))
        else:
            print(f"{str(header_file_path)} exists, not creating.",
                  file=sys.stderr)

    return 0

if __name__ == '__main__':
    sys.exit(main())
