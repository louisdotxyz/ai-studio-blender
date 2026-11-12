# SPDX-License-Identifier: GPL-3.0-or-later
"""Helpers the skills use inside execute_python (there as T and M, without importing).

T – checks: a small render from a camera, brightness, EXR preview.
M – photo match: a camera that looks like a reference photo, composite over the photo.
(V – material variants for the render queue – lives in studio/variants.py.)

Formerly separate files in <extension user dir>/tools/, loaded by every skill with
exec(open(path).read()); now part of the add-on and versioned with it.
"""

import math
import os

import bpy

from .studio import cameras


def _attachments():
    from .core import workdir
    path = os.path.join(workdir(), "attachments")
    os.makedirs(path, exist_ok=True)
    return path


def _np():
    import numpy
    return numpy


class T:
    """Checks for skills. Prefer the render_preview tool (the image comes straight back);
    T.check is for code that needs the file or the brightness."""

    @staticmethod
    def check(pct=40, samples=32, camera=None, name="check.png"):
        """Small render from the scene camera (or `camera`) into attachments/<name>.
        Returns (path, mean brightness 0-1)."""
        scene = bpy.context.scene
        render = scene.render
        keep = (scene.camera, render.resolution_percentage, scene.cycles.samples, render.filepath)
        if camera is not None:
            scene.camera = camera
        render.resolution_percentage, scene.cycles.samples = pct, samples
        out = os.path.join(_attachments(), name)
        render.filepath = out
        try:
            bpy.ops.render.render(write_still=True)
        finally:
            scene.camera, render.resolution_percentage, scene.cycles.samples, render.filepath = keep
        return out, T.mean(out)

    @staticmethod
    def mean(path):
        """Mean brightness of an image file (product photo: about 0.35-0.5)."""
        np = _np()
        image = bpy.data.images.load(path, check_existing=False)
        try:
            return float(np.array(image.pixels[:], np.float32).reshape(-1, image.channels)[:, :3].mean())
        finally:
            bpy.data.images.remove(image)

    @staticmethod
    def exr_preview(path, name="hdri.png", gain=1.3):
        """Tone map a 32-bit EXR/HDR into a PNG in attachments (to look at with Read)."""
        np = _np()
        image = bpy.data.images.load(path, check_existing=False)
        width, height = image.size
        px = np.array(image.pixels[:], np.float32).reshape(height, width, 4)[:, :, :3]
        ldr = np.clip((px / (1 + px) * gain) ** (1 / 2.2), 0, 1)
        out_image = bpy.data.images.new("_prev", width, height)
        out_image.pixels[:] = np.concatenate([ldr, np.ones((height, width, 1), np.float32)], 2).ravel()
        out = os.path.join(_attachments(), name)
        out_image.filepath_raw = out
        out_image.file_format = 'PNG'
        out_image.save()
        bpy.data.images.remove(out_image)
        bpy.data.images.remove(image)
        return out

    @staticmethod
    def cleanup():
        """Delete the temporary check images in attachments."""
        for name in ("check.png", "hdri.png", "_match_layer.png"):
            path = os.path.join(_attachments(), name)
            if os.path.exists(path):
                os.remove(path)


class M:
    """Photo match: the product rendered into a reference photo, 1:1 in framing and perspective."""

    @staticmethod
    def setup(path, name=None, from_camera=None):
        """Camera "P_match-<name>_01" with the photo as background, resolution in the photo's aspect."""
        scene = bpy.context.scene
        image = bpy.data.images.load(path, check_existing=True)
        width, height = image.size
        name = name or os.path.splitext(os.path.basename(path))[0][:24]
        source = from_camera or scene.camera
        cam = next((o for o in bpy.data.objects if o.type == 'CAMERA' and o.get("match_name") == name),
                   None) or bpy.data.objects.get("Match " + name)   # by its mark: the name follows the scheme
        if cam is None:
            data = source.data.copy() if source else bpy.data.cameras.new("Match " + name)
            data.name = "Match " + name
            cam = bpy.data.objects.new("Match " + name, data)
            (bpy.data.collections.get(cameras.camera_collection_name()) or scene.collection).objects.link(cam)
            if source:
                cam.matrix_world = source.matrix_world.copy()
            cameras.rename(cam, "match " + name)
        cam["match_name"] = name
        cd = cam.data
        cd.sensor_fit = 'AUTO'
        cd.background_images.clear()
        bg = cd.background_images.new()
        bg.image, bg.alpha, bg.frame_method = image, 1.0, 'STRETCH'
        cd.show_background_images = True
        render = scene.render
        if "orig_res" not in cam:
            cam["orig_res"] = (render.resolution_x, render.resolution_y)
        long_side = max(render.resolution_x, render.resolution_y)
        if width >= height:
            render.resolution_x, render.resolution_y = long_side, round(long_side * height / width)
        else:
            render.resolution_x, render.resolution_y = round(long_side * width / height), long_side
        cam["photo"] = path
        scene.camera = cam
        return dict(camera=cam.name, photo_px=(width, height), resolution=(render.resolution_x, render.resolution_y),
                    lens=cd.lens, location=tuple(round(v, 3) for v in cam.location))

    @staticmethod
    def set_horizon(cam, frac_from_bottom, height=None):
        """Straight verticals (rotation x = 90°), horizon at `frac_from_bottom` of the image via shift_y."""
        render = bpy.context.scene.render
        cam.rotation_euler.x = math.radians(90)
        cam.rotation_euler.y = 0
        if height is not None:
            cam.location.z = height
        aspect = render.resolution_y / render.resolution_x
        cam.data.shift_y = (0.5 - frac_from_bottom) * (1.0 if aspect >= 1 else aspect)

    @staticmethod
    def _render(cam, pct, samples):
        scene = bpy.context.scene
        render = scene.render
        settings = render.image_settings
        keep = (scene.camera, render.resolution_percentage, scene.cycles.samples, render.filepath,
                render.film_transparent, settings.file_format, settings.color_mode)
        scene.camera = cam
        render.resolution_percentage, scene.cycles.samples, render.film_transparent = pct, samples, True
        settings.file_format, settings.color_mode = 'PNG', 'RGBA'
        tmp = os.path.join(_attachments(), "_match_layer.png")
        render.filepath = tmp
        try:
            bpy.ops.render.render(write_still=True)
        finally:
            (scene.camera, render.resolution_percentage, scene.cycles.samples, render.filepath,
             render.film_transparent, settings.file_format, settings.color_mode) = keep
        return tmp

    @staticmethod
    def _composite(layer, photo, out):
        np = _np()
        fg_image = bpy.data.images.load(layer, check_existing=False)
        width, height = fg_image.size
        fg = np.array(fg_image.pixels[:], np.float32).reshape(height, width, 4)
        photo_image = bpy.data.images.load(photo, check_existing=False)
        photo_image.scale(width, height)
        bg = np.array(photo_image.pixels[:], np.float32).reshape(height, width, 4)[:, :, :3]
        alpha = fg[:, :, 3:4]

        def lin(c):
            return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)

        def srgb(c):
            return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.clip(c, 0, None) ** (1 / 2.4) - 0.055)
        result = srgb(lin(fg[:, :, :3]) * alpha + lin(bg) * (1 - alpha))
        out_image = bpy.data.images.new("_match_out", width, height)
        out_image.pixels[:] = np.concatenate([np.clip(result, 0, 1), np.ones((height, width, 1), np.float32)],
                                             2).ravel()
        out_image.filepath_raw = out
        out_image.file_format = 'PNG'
        out_image.save()
        for image in (fg_image, photo_image, out_image):
            bpy.data.images.remove(image)
        os.remove(layer)
        return out, float(result.mean())

    @staticmethod
    def check(cam=None, pct=40, samples=32):
        """Small render, cut out (transparent film, shadow catcher) and laid over the photo.
        Returns (attachments/check.png, mean brightness)."""
        cam = cam or bpy.context.scene.camera
        return M._composite(M._render(cam, pct, samples), cam["photo"], os.path.join(_attachments(), "check.png"))

    @staticmethod
    def final(cam=None, samples=None, folder="~/Downloads"):
        """Full resolution over the photo, as PNG into `folder`."""
        cam = cam or bpy.context.scene.camera
        scene = bpy.context.scene
        blend = bpy.path.basename(bpy.data.filepath)[:-6] or "untitled"
        out = os.path.join(os.path.expanduser(folder), "%s_%s.png" % (cam.name.replace(" ", "_"), blend))
        return M._composite(M._render(cam, 100, samples or scene.cycles.samples), cam["photo"], out)

    @staticmethod
    def restore(cam):
        """The resolution from before setup()."""
        if "orig_res" in cam:
            render = bpy.context.scene.render
            render.resolution_x, render.resolution_y = cam["orig_res"]

    @staticmethod
    def project(cam, objects):
        """Extend the view: project the photo from the match camera onto proxy geometry (floor,
        walls). Outside the photo the material shows the photo's mean color."""
        np = _np()
        image = bpy.data.images.load(cam["photo"], check_existing=True)
        width, height = image.size
        name = "ENV Foto " + cam.name[6:]
        mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
        nt = mat.node_tree
        nt.nodes.clear()
        nodes, links = nt.nodes, nt.links
        out = nodes.new("ShaderNodeOutputMaterial")
        out.location = (700, 0)
        uv = nodes.new("ShaderNodeUVMap")
        uv.uv_map, uv.location = "Foto", (-600, 0)
        tex = nodes.new("ShaderNodeTexImage")
        tex.image, tex.extension, tex.location = image, 'CLIP', (-350, 0)
        links.new(uv.outputs[0], tex.inputs[0])
        emission = nodes.new("ShaderNodeEmission")
        emission.location = (0, 100)
        links.new(tex.outputs[0], emission.inputs[0])
        mix = nodes.new("ShaderNodeMixShader")
        mix.location = (400, 0)
        links.new(tex.outputs["Alpha"], mix.inputs[0])
        links.new(emission.outputs[0], mix.inputs[2])
        mean = np.array(image.pixels[:], np.float32).reshape(-1, image.channels)[:, :3].mean(0)
        fill = nodes.new("ShaderNodeBsdfPrincipled")
        fill.location = (0, -200)
        fill.inputs["Base Color"].default_value = (*[float(c) ** 2.2 for c in mean], 1)
        fill.inputs["Roughness"].default_value = 0.85
        links.new(fill.outputs[0], mix.inputs[1])
        links.new(mix.outputs[0], out.inputs[0])
        for obj in objects:
            mesh = obj.data
            if "Foto" not in mesh.uv_layers:
                mesh.uv_layers.new(name="Foto")
            mod = obj.modifiers.get("Foto Projektion") or obj.modifiers.new("Foto Projektion", 'UV_PROJECT')
            mod.uv_layer, mod.projector_count = "Foto", 1
            mod.projectors[0].object = cam
            mod.aspect_x, mod.aspect_y = (width / height, 1.0) if width >= height else (1.0, height / width)
            if mesh.materials:
                mesh.materials[0] = mat
            else:
                mesh.materials.append(mat)
        return mat.name


def namespace():
    """Names available in execute_python without importing."""
    names = {"T": T, "M": M}
    try:
        from .studio import api, variants
        names.update(V=variants.V, api=api)
    except ImportError:
        pass
    return names
