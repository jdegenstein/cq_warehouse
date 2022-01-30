"""
Build CadQuery Patch

name: build_cadquery_patch.py
by:   Gumyr
date: January 27th 2022

desc:

    In order for cq_warehouse CadQuery extensions to be recognized by IDEs (e.g. Intellisense)
    the CadQuery source code needs to be updated. Although monkeypatching allows
    for the functionality of CadQuery to be extended, these extensions are not
    visible to the IDE which makes working with them more difficult.

    This code takes the cq_warehouse extensions.py file, reformats it to fit into
    the CadQuery source code, applies changes to official Cadquery source files
    and generates extended versions of these files:
    - assembly.py,
    - cq.py,
    - geom.py, and
    - shapes.py.
    Finally, a diff is generated between the originals and extended files for use
    with the patch command.

    Usage:
        > python3 build_cadquery_patch <path_to_cadquery_installation>

todo: Add support for extension methods with decorators

license:

    Copyright 2022 Gumyr

    Licensed under the Apache License, Version 2.0 (the "License");
    you may not use this file except in compliance with the License.
    You may obtain a copy of the License at

        http://www.apache.org/licenses/LICENSE-2.0

    Unless required by applicable law or agreed to in writing, software
    distributed under the License is distributed on an "AS IS" BASIS,
    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
    See the License for the specific language governing permissions and
    limitations under the License.

"""
import sys
import getopt
import os
import re
from typing import Literal, Union
import subprocess
import tempfile
import shutil

# Which CadQuery files define the Class
class_files = {
    "occ_impl/shapes.py": ["Shape", "Vertex", "Edge", "Wire", "Face", "Module"],
    "assembly.py": ["Assembly"],
    "cq.py": ["Workplane"],
    "occ_impl/geom.py": ["Plane", "Vector"],
}


def increase_indent(amount: int, python_code: list[str]) -> list[str]:
    """Increase indentation

    Increase the indentation of the code by a given number of spaces

    Args:
        amount (int): number of spaces to indent
        python_code (list[str]): code to indent

    Returns:
        list[str]: indented code
    """
    return [" " * amount + line for line in python_code]


def code_location(
    object_name: str,
    object_type: Literal["class", "method", "function"],
    python_code: list[str],
    range: tuple[int, int] = (0, 1000000),
) -> Union[tuple[int, int], None]:
    """locate python code within a module

    Finds the start and end lines for a class, method or function within a
    larger python module. Method names must be specificed as 'class.method'
    to ensure they are unique.

    Args:
        object_name (str): name of python function - methods are specified as class.method
        object_type (Literal["class","method","function"]): type of code object to extract
        python_code (list[str]): python code
        range (range: tuple[int, int]): search range. Defaults to entire module.

    Raises:
        ValueError: invalid object type
        ValueError: badly formed method name

    Returns:
        Union[tuple[int, int],None]: either a (start,end) tuple or None if not found
    """
    if object_type not in ["class", "method", "function"]:
        raise ValueError("object type must be one of 'class', 'method', or 'function'")

    if object_type == "method" and len(object_name.split(".")) != 2:
        raise ValueError("method names must be specified as 'class.method'")

    object_dictionary = {"class": "class", "method": "def", "function": "def"}

    # Methods are only unique within a class, so extract from just the class code
    if object_type == "method":
        class_name, object_to_find = tuple(object_name.split("."))
        search_start, search_end = code_location(class_name, "class", python_code)
    else:
        object_to_find = object_name
        search_start, search_end = range

    object_key_word = object_dictionary[object_type]
    if object_type == "function":
        object_pattern = re.compile(rf"^{object_key_word}\s+{object_to_find}\(")
    else:
        object_pattern = re.compile(rf"^\s*{object_key_word}\s+{object_to_find}\(")

    line_numbers = []
    found = False
    for line_number, line in enumerate(python_code):
        # method are only unique within a class
        if not (search_start < line_number < search_end):
            continue
        if not found:
            found = object_pattern.match(line)
            if found:
                indent = re.match(r"^\s*", line).group()
                # this regex is a negative lookahead assertion that looks
                # for non white space or a non closing brace (from the input parameters)
                end_of_function_pattern = re.compile(rf"^{indent}(?!\s|\))")
        else:
            found = not end_of_function_pattern.match(line)
        if found:
            line_numbers.append(line_number)

    if line_numbers:
        locations = (line_numbers[0], line_numbers[-1])
    else:
        locations = None
    return locations


def extract_code(
    object_name: str,
    object_type: Literal["class", "method", "function"],
    python_code: list[str],
) -> list[str]:
    """Extract a class, method or function from python code

    Args:
        object_name (str): name of python function - methods are specified as class.method
        object_type (Literal["class","method","function"]): type of code object to extract
        python_code (list[str]): python code

    Returns:
        list[str]: code from just this object
    """
    code_range = code_location(object_name, object_type, python_code)
    if code_range is None:
        object_code = []
    else:
        object_code = python_code[code_range[0] : code_range[1]]
    return object_code


def prepare_extensions(python_code: list[str]) -> dict[list[dict]]:
    """Prepare monkeypatched file

    Return a data structure with the python code separated by class and method
    with the monkeypatched method name replacing the function name.
    dict[class:list[dict[method:code]]]

    Args:
        python_code (list[str]): original python code

    Returns:
        dict[list[dict]]: converted python code organized by class and method
    """
    # Find all functions
    all_functions = []
    function_pattern = re.compile(r"^def\s+([a-zA-Z_]+)\(")
    for line_num, line in enumerate(python_code):
        function_match = function_pattern.match(line)
        if function_match:
            all_functions.append(function_match.group(1))

    # Build a monkeypatch dictionary of {function: class.method}
    monkeypatch_pattern = re.compile(
        r"^([A-Z][a-zA-Z_]*.[a-zA-Z_]+)\s*=\s*([a-zA-Z_]+)\s*$"
    )
    monkeypatches = {}
    monkeypatch_line_numbers = []
    for line_num, line in enumerate(python_code):
        monkeypatch_match = monkeypatch_pattern.match(line)
        if monkeypatch_match:
            monkeypatches[monkeypatch_match.group(2)] = monkeypatch_match.group(1)
            monkeypatch_line_numbers.append(line_num)

    # Find the real functions that aren't monkeypatched into a class
    pure_functions = [f for f in all_functions if f not in list(monkeypatches.keys())]

    # Remove the monkey patches from the code
    monkeypatch_line_numbers.reverse()
    for line_num in monkeypatch_line_numbers:
        python_code.pop(line_num)

    # Separate the code into return data structure
    code_dictionary = {}
    for function_name, class_method in monkeypatches.items():
        method_code = {}
        class_name, _sep, method_name = class_method.partition(".")
        method_code[method_name] = extract_code(function_name, "function", python_code)
        method_code[method_name][0] = method_code[method_name][0].replace(
            function_name, method_name
        )
        if class_name in code_dictionary:
            code_dictionary[class_name].append(method_code)
        else:
            code_dictionary[class_name] = [method_code]

    for function_name in pure_functions:
        function_code = {}
        function_code[function_name] = extract_code(
            function_name, "function", python_code
        )
        if "Module" in code_dictionary:
            code_dictionary["Module"].append(function_code)
        else:
            code_dictionary["Module"] = [function_code]

    return code_dictionary


def main(argv):
    # Parse the command line inputs
    cadquery_path = ""
    try:
        opts, args = getopt.getopt(argv, "hp:", ["help", "path="])
    except getopt.GetoptError:
        print("prepare_patch.py -p <path_to_cadquery>")
        sys.exit(2)
    for opt, arg in opts:
        if opt == "-h":
            print("prepare_patch.py -p <path_to_cadquery>")
            sys.exit()
        elif opt in ("-p", "--path"):
            cadquery_path = arg
    if not opts:
        cadquery_path = args[0]

    # Does the cadquery path exist and point to cadquery
    if not os.path.isfile(cadquery_path + "cq.py"):
        print(f"{cadquery_path} is invalid - cq.py should be in this directory")
        sys.exit(2)

    # Read the extensions.py file
    with open("../src/cq_warehouse/extensions.py") as f:
        extensions_python_code = f.readlines()

    # Organize the extensions monkeypatched code into class(s), method(s)
    code_dictionary = prepare_extensions(extensions_python_code)

    # Prepare a location to diff the original and extended versions
    temp_directory = tempfile.TemporaryDirectory()
    temp_directory_path = temp_directory.name
    original_directory_path = os.path.join(temp_directory_path, "original")
    extended_directory_path = os.path.join(temp_directory_path, "extensions")
    shutil.copytree(cadquery_path, original_directory_path)
    shutil.copytree(cadquery_path, extended_directory_path)

    # Update existing methods and add new ones for each of the source files
    for file_name in class_files.keys():
        source_file_location = os.path.join(cadquery_path, file_name)
        with open(source_file_location) as f:
            source_code = f.readlines()

        for class_name in class_files[file_name]:
            method_dictionaries = code_dictionary[class_name]
            extension_methods = []
            for method_dictionary in method_dictionaries:
                for method_name, method_code in method_dictionary.items():
                    # One weird fix - need to uncomment out this line
                    if class_name == "Plane" and method_name == "toLocalCoords":
                        method_code = [
                            line.replace(
                                "# from .shapes import Shape",
                                "from .shapes import Shape",
                            )
                            for line in method_code
                        ]
                    if class_name == "Module":
                        code_range = code_location(method_name, "function", source_code)
                    else:
                        code_range = code_location(
                            class_name + "." + method_name, "method", source_code
                        )
                    if code_range is None and class_name == "Module":
                        source_size = len(source_code)
                        source_code[source_size:source_size] = method_code
                    elif code_range is None:
                        extension_methods.append(method_name)
                    else:
                        # Delete the old code
                        del source_code[code_range[0] : code_range[1]]
                        # Insert the new code
                        source_code[code_range[0] : code_range[0]] = increase_indent(
                            4, method_code
                        )

            # Create the code block that needs to be inserted into this class
            extension_code = []
            for extension_method in extension_methods:
                for method_dictionary in method_dictionaries:
                    if extension_method in method_dictionary.keys():
                        extension_code.extend(method_dictionary[extension_method])
            extension_code = increase_indent(4, extension_code)

            if class_name == "Module":
                class_end = len(source_code) - 1
            else:
                _class_start, class_end = code_location(
                    class_name, "class", source_code
                )
            source_code[class_end + 1 : class_end + 1] = extension_code

        # Write extended source file
        extended_file_name = (
            os.path.basename(file_name).split(".py")[0] + "_extended.py"
        )
        f = open(extended_file_name, "w")
        f.writelines(source_code)
        f.close()

        # Run black on the resulting file to ensure formatting is correct
        # .. danger of format changes polluting the patch
        # subprocess.run(["black", output_file_name])

        # Replace the original files in the extensions temp directory
        shutil.copyfile(
            extended_file_name, os.path.join(extended_directory_path, file_name)
        )

    # Create the patch file
    with open("cadquery_extensions.patch", "w") as patch_file:
        subprocess.run(
            [
                "diff",
                "-rN",
                "-U",
                "5",
                original_directory_path,
                extended_directory_path,
            ],
            stdout=patch_file,
        )
    # Copy the patch to the cadquery original source directory
    shutil.copyfile(
        "cadquery_extensions.patch",
        os.path.join(cadquery_path, "cadquery_extensions.patch"),
    )

    print(
        "Created the cadquery_extensions.patch file and copied it to cadquery source directory"
    )
    print("To apply the patch:")
    print(f"cd {cadquery_path}")
    print("patch -s -p0 < cadquery_extensions.patch")


if __name__ == "__main__":
    main(sys.argv[1:])